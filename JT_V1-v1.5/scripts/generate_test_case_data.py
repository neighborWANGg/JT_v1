from datetime import datetime, timedelta
from pathlib import Path
import sys

from openpyxl import Workbook


def build(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    chat = workbook.active
    chat.title = "聊天记录"
    chat.append(["序号", "时间", "群ID", "微信号", "聊天内容", "IP登录地址"])
    people = [
        ("周凯", "wxid_zhou_kai", "本人", "39.45.3.227", "负责联系饭局与后续安排"),
        ("刘桂香", "wxid_liu_gui_xiang", "母亲", "60.191.23.96", "负责提醒家中长辈与礼数"),
        ("刘洋", "wxid_liu_yang", "同事", "223.104.18.220", "负责记录账目和现场信息"),
    ]
    dialogue = [
        "菜先上，话慢慢说，今天只谈合作。", "这桌菜看着热闹，账目还是要一项一项核清。",
        "人情可以记在心里，数字必须写在纸上。", "先把来意讲明白，免得大家猜来猜去。",
        "我敬大家一杯，感谢各位抽时间到场。", "酒不急着喝，事情得先摆到台面上。",
        "这道鱼寓意年年有余，合同也要留有余地。", "谁负责哪一段，今晚最好当面定下来。",
        "你说得有道理，但证据和凭据也不能少。", "饭局是饭局，规矩不能因为热闹就省掉。",
        "我把清单带来了，大家可以逐条核对。", "如果有不同意见，现在提最合适。",
        "先听完再下结论，别让一句话盖过全部事实。", "这杯酒我先干，后面的流程按计划走。",
        "合作要长久，透明比客气更重要。", "今天的决定写进纪要，明天照着执行。",
        "家常话可以多说，关键节点不能含糊。", "钱从哪里来、到哪里去，都要留痕。",
        "我不怕大家问得细，就怕事情说不清。", "大家都是熟人，更要把边界说清楚。",
        "菜凉了可以再热，遗漏的记录不好补。", "这顿饭的重点不是喝多少，是把事办稳。",
    ]
    start = datetime(2026, 9, 14, 18, 30)
    group_id = "99001888"
    for index in range(240):
        person = people[index % len(people)]
        text = dialogue[index % len(dialogue)]
        if index % 17 == 0:
            text = f"{text} 第{index // 17 + 1}项安排请记入清单。"
        chat.append([index + 1, start + timedelta(minutes=index * 2), group_id, person[1], text, person[3]])

    relation = workbook.create_sheet("人员关系")
    relation.append(["序号", "姓名", "微信号", "关系", "IP地址", "关系说明"])
    for index, (name, wechat, relation_name, ip, note) in enumerate(people, 1):
        relation.append([index, name, wechat, relation_name, ip, note])

    chat.freeze_panes = "A2"
    relation.freeze_panes = "A2"
    workbook.save(path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate_test_case_data.py OUTPUT.xlsx")
    build(Path(sys.argv[1]))
