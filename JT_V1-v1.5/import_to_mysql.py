"""
从 Excel 导入人员信息与群聊信息到 MySQL（JT 库）。

导入前需输入案件编号、案件名称；写入数据库的每一行都会带上这两个字段。
人员表：Excel 每一行直接写入 persons；remark 按「周凯的妻子」格式生成。
群聊表：读取聊天记录并自动生成 BGE-M3 向量写入 chat_messages。

用法：
  python import_to_mysql.py              # 默认：人员 + 全部群聊（运行时输入案件信息）
  python import_to_mysql.py persons      # 仅人员
  python import_to_mysql.py chat         # 仅群聊
  python import_to_mysql.py all          # 人员 + 全部群聊
  python import_to_mysql.py all --case-number AJ2026-001 --case-name 酒桶专案

环境变量（或运行时在 .env 中配置）：
  MYSQL_HOST=127.0.0.1
  MYSQL_PORT=3306
  MYSQL_USER=root
  MYSQL_PASSWORD=你的密码
  MYSQL_DATABASE=JT
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from getpass import getpass
from pathlib import Path

import pandas as pd
import pymysql
from pymysql.cursors import DictCursor
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent
DEFAULT_PERSON_FILE = ROOT / "信息表" / "信息表.xlsx"
DEFAULT_CHAT_FILE = ROOT / "信息表" / "微信聊天记录测试数据 (3).xlsx"
MODEL_PATH = ROOT / "model" / "bge-m3"
CHAT_SHEET = "聊天记录"
BATCH_SIZE = 8
MAX_LENGTH = 512

PERSON_COLUMNS = {
    "name": ("姓名",),
    "wx_nickname": ("微信昵称",),
    "wechat_id": ("微信号",),
    "gender": ("性别",),
    "id_card": ("身份证号",),
    "phone": ("手机号",),
    "delivery_address": ("快递地址", "外卖地址"),
    "ip_address": ("常用IP", "IP登录地址", "登录IP"),
    "case_info": ("案件信息",),
}

SELF_MARKERS = ("本人", "主体", "主要人员")
OWNER_COLUMNS = ("所属主要人员身份证", "主要人员身份证", "主体身份证号")


@dataclass(frozen=True)
class CaseInfo:
    case_number: str
    case_name: str


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_db_config() -> dict:
    load_dotenv()
    password = os.getenv("MYSQL_PASSWORD")
    if password is None:
        password = getpass("MySQL 密码（直接回车表示无密码）：")
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": password or "",
        "database": os.getenv("MYSQL_DATABASE", "JT"),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }


def resolve_case_info(case_number: str | None, case_name: str | None) -> CaseInfo:
    number = (case_number or "").strip()
    name = (case_name or "").strip()
    if not number:
        number = input("请输入案件编号：").strip()
    if not name:
        name = input("请输入案件名称：").strip()
    if not number:
        raise SystemExit("案件编号不能为空。")
    if not name:
        raise SystemExit("案件名称不能为空。")
    print(f"\n当前案件：{name}（编号 {number}）\n")
    return CaseInfo(case_number=number, case_name=name)


def connect_db():
    cfg = get_db_config()
    print(f"正在连接 MySQL：{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']} …")
    try:
        conn = pymysql.connect(**cfg)
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        if code == 2003:
            raise SystemExit(
                "无法连接 MySQL（10061 / Communications link failure）。\n"
                "请先启动 MySQL 服务，再在 DataGrip 里 Test Connection 确认能连上。\n"
                "本机 MySQL 路径示例：G:\\app_setup\\mysql2\\mysql-8.0.26-winx64\\mysql-8.0.26-winx64\\bin\\mysqld.exe"
            ) from exc
        if code == 1045:
            raise SystemExit(
                "MySQL 账号或密码错误（1045）。\n"
                "请在项目根目录创建 .env，填入 DataGrip 里能登录的 MYSQL_USER 和 MYSQL_PASSWORD。"
            ) from exc
        raise
    conn.select_db(cfg["database"])
    print("MySQL 连接成功。")
    return conn


def pick_column(df: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    for name in aliases:
        if name in df.columns:
            return name
    return None


def find_relation_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if "关系" in str(col):
            return col
    return None


def find_owner_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if col in OWNER_COLUMNS:
            return col
        text = str(col)
        if "主要人员" in text and "身份证" in text:
            return col
    return None


def normalize_id_card(value) -> str:
    text = str(value).strip().upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def normalize_gender(value) -> str:
    text = str(value or "").strip()
    if text in ("男", "女"):
        return text
    return "未知"


def birth_from_id_card(id_card: str):
    if len(id_card) == 18 and id_card[:17].isdigit() and id_card[-1] in "0123456789X":
        try:
            return datetime.strptime(id_card[6:14], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def build_wechat_info(row: pd.Series, wx_col: str | None, nick_col: str | None, phone_col: str | None) -> str:
    payload = {}
    if wx_col and pd.notna(row.get(wx_col)):
        payload["wxid"] = str(row[wx_col]).strip()
    if nick_col and pd.notna(row.get(nick_col)):
        payload["nickname"] = str(row[nick_col]).strip()
    if phone_col and pd.notna(row.get(phone_col)):
        payload["phone"] = str(row[phone_col]).strip()
    return json.dumps(payload, ensure_ascii=False)


def read_person_excel(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"找不到人员 Excel：{path}")
    df = pd.read_excel(path)
    id_col = pick_column(df, PERSON_COLUMNS["id_card"])
    name_col = pick_column(df, PERSON_COLUMNS["name"])
    if not id_col or not name_col:
        raise ValueError(f"人员表缺少「姓名」或「身份证号」列，当前列：{list(df.columns)}")
    df = df.copy()
    df[id_col] = df[id_col].map(normalize_id_card)
    df = df[df[id_col].astype(str).str.len() >= 15].copy()
    df.reset_index(drop=True, inplace=True)
    return df


def row_owner_id(row: pd.Series, owner_col: str | None) -> str | None:
    if not owner_col or owner_col not in row.index or pd.isna(row.get(owner_col)):
        return None
    owner = normalize_id_card(row[owner_col])
    return owner or None


def anchor_names_by_id(df: pd.DataFrame, rel_col: str | None, name_col: str, id_col: str) -> dict[str, str]:
    """关系列为「本人/主体/主要人员」的行，用作 remark 里的 xxx（如 周凯的妻子）。"""
    if not rel_col:
        return {}
    anchors: dict[str, str] = {}
    for _, row in df.iterrows():
        relation = str(row.get(rel_col, "")).strip()
        if relation in SELF_MARKERS:
            anchors[str(row[id_col])] = str(row[name_col]).strip()
    return anchors


def build_person_remark(
    row: pd.Series,
    rel_col: str | None,
    owner_col: str | None,
    anchors: dict[str, str],
) -> str | None:
    if not rel_col or pd.isna(row.get(rel_col)):
        return None
    relation = str(row[rel_col]).strip()
    if not relation or relation in SELF_MARKERS:
        return None

    owner_id = row_owner_id(row, owner_col)
    if owner_id and owner_id in anchors:
        return f"{anchors[owner_id]}的{relation}"
    if len(anchors) == 1:
        return f"{next(iter(anchors.values()))}的{relation}"
    return relation


def upsert_person(cursor, row: pd.Series, case: CaseInfo, remark: str | None = None) -> None:
    def col(name_aliases: tuple[str, ...]) -> str | None:
        for alias in name_aliases:
            if alias in row.index:
                return alias
        return None

    id_col = col(PERSON_COLUMNS["id_card"])
    name_col = col(PERSON_COLUMNS["name"])
    if not id_col or not name_col:
        raise ValueError("缺少姓名或身份证号列")

    id_card = normalize_id_card(row[id_col])
    name = str(row[name_col]).strip()
    gender_col = col(PERSON_COLUMNS["gender"])
    gender = normalize_gender(row[gender_col] if gender_col else None)
    birth_date = birth_from_id_card(id_card)

    delivery_parts = []
    for alias in PERSON_COLUMNS["delivery_address"]:
        if alias in row.index and pd.notna(row[alias]) and str(row[alias]).strip():
            delivery_parts.append(f"{alias}：{str(row[alias]).strip()}")
    delivery_address = "；".join(delivery_parts) if delivery_parts else None

    ip_col = col(PERSON_COLUMNS["ip_address"])
    ip_address = str(row[ip_col]).strip() if ip_col and pd.notna(row.get(ip_col)) else None

    case_col = col(PERSON_COLUMNS["case_info"])
    case_info = str(row[case_col]).strip() if case_col and pd.notna(row.get(case_col)) else None

    wechat_info = build_wechat_info(
        row,
        col(PERSON_COLUMNS["wechat_id"]),
        col(PERSON_COLUMNS["wx_nickname"]),
        col(PERSON_COLUMNS["phone"]),
    )

    cursor.execute(
        """
        INSERT INTO persons (
            case_number, case_name, id_card, name, gender, birth_date,
            delivery_address, ip_address, wechat_info, case_info, remark
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            case_name=VALUES(case_name),
            name=VALUES(name),
            gender=VALUES(gender),
            birth_date=VALUES(birth_date),
            delivery_address=VALUES(delivery_address),
            ip_address=VALUES(ip_address),
            wechat_info=VALUES(wechat_info),
            case_info=VALUES(case_info),
            remark=VALUES(remark)
        """,
        (
            case.case_number,
            case.case_name,
            id_card,
            name,
            gender,
            birth_date,
            delivery_address,
            ip_address,
            wechat_info,
            case_info,
            remark,
        ),
    )


def import_persons(person_file: Path, case: CaseInfo) -> None:
    df = read_person_excel(person_file)
    name_col = pick_column(df, PERSON_COLUMNS["name"]) or "姓名"
    id_col = pick_column(df, PERSON_COLUMNS["id_card"]) or "身份证号"
    rel_col = find_relation_column(df)
    owner_col = find_owner_column(df)
    anchors = anchor_names_by_id(df, rel_col, name_col, id_col)

    conn = connect_db()
    try:
        with conn.cursor() as cursor:
            for _, row in df.iterrows():
                remark = build_person_remark(row, rel_col, owner_col, anchors)
                upsert_person(cursor, row, case, remark=remark)
                name = str(row[name_col]).strip()
                if remark:
                    print(f"  {name}（{row[id_col]}）→ remark: {remark}")
                else:
                    print(f"  {name}（{row[id_col]}）")
        conn.commit()
    finally:
        conn.close()

    print(f"\n人员导入完成，共 {len(df)} 人。")


def load_bge_model():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"找不到 BGE-M3 模型：{MODEL_PATH}\n请先运行 python down_model.py")
    try:
        import torch
        use_cuda = torch.cuda.is_available()
    except ImportError:
        use_cuda = False
    from FlagEmbedding import BGEM3FlagModel

    print(f"正在加载 BGE-M3（{'GPU' if use_cuda else 'CPU'}）…")
    kwargs = {"use_fp16": use_cuda, "query_max_length": MAX_LENGTH}
    if use_cuda:
        kwargs["devices"] = "cuda:0"
    return BGEM3FlagModel(str(MODEL_PATH), **kwargs)


def stable_message_id(case_number: str, file_hash: str, sheet: str, row_number: int) -> str:
    payload = f"{case_number}:{file_hash}:{sheet}:{row_number}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_wechat_ids(case: CaseInfo) -> set[str]:
    conn = connect_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT wechat_info FROM persons WHERE case_number = %s",
                (case.case_number,),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    wxids: set[str] = set()
    for row in rows:
        raw = row.get("wechat_info")
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("wxid"):
            wxids.add(str(data["wxid"]).strip())
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, str) and item.strip():
                    wxids.add(item.strip())
    return wxids


def parse_sent_at(value):
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def import_chat(chat_file: Path, scope: str, case: CaseInfo) -> None:
    if not chat_file.exists():
        raise FileNotFoundError(f"找不到群聊 Excel：{chat_file}")

    df = pd.read_excel(chat_file, sheet_name=CHAT_SHEET)
    required = ["时间", "群ID", "微信号", "聊天内容", "IP登录地址"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"群聊表缺少字段：{missing}，当前列：{list(df.columns)}")

    df["聊天内容"] = df["聊天内容"].fillna("").astype(str)
    df = df[df["聊天内容"].str.strip() != ""].copy()
    df.reset_index(drop=True, inplace=True)

    allowed_wxids: set[str] | None = None
    if scope == "related":
        allowed_wxids = collect_wechat_ids(case)
        if not allowed_wxids:
            raise RuntimeError("数据库里还没有人员微信号，请先执行 persons 导入。")
        before = len(df)
        df = df[df["微信号"].astype(str).isin(allowed_wxids)].copy()
        print(f"按人员表微信号筛选：{before} -> {len(df)} 条")
        if df.empty:
            print("筛选后没有可导入的聊天记录。")
            return

    model = load_bge_model()
    file_hash = file_sha256(chat_file)
    source_name = chat_file.name

    conn = connect_db()
    try:
        texts = df["聊天内容"].tolist()
        all_vectors: list[list[float]] = []
        for start in tqdm(range(0, len(texts), BATCH_SIZE), desc="BGE-M3 向量化"):
            batch = texts[start : start + BATCH_SIZE]
            output = model.encode(
                batch,
                batch_size=BATCH_SIZE,
                max_length=MAX_LENGTH,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
            for vector in output["dense_vecs"]:
                all_vectors.append([float(x) for x in vector])

        with conn.cursor() as cursor:
            for i, (_, row) in enumerate(df.iterrows()):
                excel_row = int(row["序号"]) if "序号" in df.columns and pd.notna(row.get("序号")) else i + 2
                message_id = stable_message_id(case.case_number, file_hash, CHAT_SHEET, excel_row)
                cursor.execute(
                    """
                    INSERT INTO chat_messages (
                        case_number, case_name, message_id, wechat_id, sent_at, group_id, content,
                        ip_address, embedding, source_file, source_row
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        case_name=VALUES(case_name),
                        wechat_id=VALUES(wechat_id),
                        sent_at=VALUES(sent_at),
                        group_id=VALUES(group_id),
                        content=VALUES(content),
                        ip_address=VALUES(ip_address),
                        embedding=VALUES(embedding),
                        source_file=VALUES(source_file),
                        source_row=VALUES(source_row)
                    """,
                    (
                        case.case_number,
                        case.case_name,
                        message_id,
                        str(row["微信号"]).strip(),
                        parse_sent_at(row["时间"]),
                        str(row["群ID"]).strip() if pd.notna(row.get("群ID")) else None,
                        str(row["聊天内容"]).strip(),
                        str(row["IP登录地址"]).strip() if pd.notna(row.get("IP登录地址")) else None,
                        json.dumps(all_vectors[i]),
                        source_name,
                        excel_row,
                    ),
                )
        conn.commit()
    finally:
        conn.close()

    print(f"群聊导入完成，共 {len(df)} 条。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导入人员与群聊 Excel 到 MySQL")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("persons", "chat", "all"),
        default="all",
        help="persons=仅人员；chat=仅群聊；all=先人员后群聊（默认）",
    )
    parser.add_argument("--person-file", type=Path, default=DEFAULT_PERSON_FILE)
    parser.add_argument("--chat-file", type=Path, default=DEFAULT_CHAT_FILE)
    parser.add_argument("--case-number", type=str, default=None, help="案件编号（不填则运行时输入）")
    parser.add_argument("--case-name", type=str, default=None, help="案件名称（不填则运行时输入）")
    parser.add_argument(
        "--chat-scope",
        choices=("related", "all"),
        default="all",
        help="all=导入 Excel 全部聊天（默认）；related=只导入人员表里已有微信号的消息",
    )
    return parser


def main() -> None:
    configure_stdout()
    print("JT 数据导入脚本已启动…")
    args = build_parser().parse_args()
    print(f"模式：{args.mode}，群聊范围：{args.chat_scope}")
    case = resolve_case_info(args.case_number, args.case_name)

    if args.mode in ("persons", "all"):
        import_persons(args.person_file, case)

    if args.mode in ("chat", "all"):
        import_chat(args.chat_file, args.chat_scope, case)


if __name__ == "__main__":
    main()
