from datetime import datetime, timedelta
import uuid

from app.investigation import (
    ChatItem,
    MysqlPerson,
    build_group_profiles,
    build_llm_briefing,
    classify_groups_heuristic,
    evidence_harness,
    lexical_hit,
    screen_wechat_friends,
    summarize_persons,
)
from app.mysql_repo import WeChatAccount


def test_evidence_harness_finds_single_character_fact_and_context():
    source_id = uuid.uuid4()
    start = datetime(2026, 9, 14, 18, 30)
    rows = [
        ChatItem(f"m{index}", source_id, index + 2, start + timedelta(minutes=index), "g1", "u1", content, None)
        for index, content in enumerate(["菜已经上桌", "这道鱼寓意年年有余", "大家开始商量后续安排"])
    ]
    hits = evidence_harness("饭局上是否有鱼", rows, limit=1, before=1, after=1)
    assert hits[0].content == "这道鱼寓意年年有余"
    assert {hit.message_id for hit in hits} == {"m0", "m1", "m2"}


def _person(**kwargs) -> MysqlPerson:
    defaults = dict(
        id_card="330881198805125172",
        name="周凯",
        gender="男",
        birth_date="1988-05-12",
        delivery_address="杭州市",
        ip_address="1.1.1.1",
        wechat_id="wxid_zhoukai",
        wx_nickname="老K",
        phone="13800000000",
        case_info=None,
        remark=None,
    )
    defaults.update(kwargs)
    return MysqlPerson(**defaults)


def test_summarize_subject_then_relations():
    persons = [
        _person(),
        _person(id_card="330881199001011111", name="李四", wechat_id="wxid_lisi", remark="周凯的妻子"),
        _person(id_card="330881196001011111", name="周父", wechat_id="wxid_father", remark="周凯的父亲"),
    ]
    lines, subjects, related = summarize_persons(persons)
    assert subjects[0].name == "周凯"
    assert [item.name for item in related] == ["李四", "周父"]
    assert lines[0].startswith("【本人】周凯")
    assert "身份证号：330881198805125172" in lines[0]
    assert any("周凯的妻子" in line for line in lines)
    assert any("周凯的父亲" in line for line in lines)


def test_lexical_keyword_keeps_all_hits():
    assert lexical_hit("蓝盒", "蓝盒还在原处吗")
    assert not lexical_hit("蓝盒", "昨晚没动，等通知")


def test_group_profiles_sorted_by_speak_and_related():
    items = [
        ChatItem(
            message_id="1", source_file_id=__import__("uuid").uuid4(), source_row=1,
            sent_at=None, group_id="family", sender_id="wxid_zhoukai",
            content="晚上回家吃饭", ip_address=None,
        ),
        ChatItem(
            message_id="2", source_file_id=__import__("uuid").uuid4(), source_row=2,
            sent_at=None, group_id="family", sender_id="wxid_lisi",
            content="好的", ip_address=None,
        ),
        ChatItem(
            message_id="3", source_file_id=__import__("uuid").uuid4(), source_row=3,
            sent_at=None, group_id="work", sender_id="wxid_zhoukai",
            content="今天下午开会", ip_address=None,
        ),
        ChatItem(
            message_id="4", source_file_id=__import__("uuid").uuid4(), source_row=4,
            sent_at=None, group_id="work", sender_id="wxid_zhoukai",
            content="纪要发群里", ip_address=None,
        ),
        ChatItem(
            message_id="5", source_file_id=__import__("uuid").uuid4(), source_row=5,
            sent_at=None, group_id="work", sender_id="wxid_lisi",
            content="收到", ip_address=None,
        ),
    ]
    subjects = [_person()]
    related = [_person(id_card="2", name="李四", wechat_id="wxid_lisi", remark="周凯的妻子")]
    profiles = classify_groups_heuristic(build_group_profiles(items, subjects, related))
    assert [item.group_id for item in profiles] == ["work", "family"]
    assert profiles[0].subject_speak_count == 2
    assert profiles[0].related_count == 1
    assert profiles[1].subject_speak_count == 1
    briefing = build_llm_briefing([subjects[0], related[0]], profiles)
    assert "crime" not in str(briefing["groups"]).lower()
    assert briefing["groups"][0]["rank"] == 1
    assert briefing["groups"][0]["group_id"] == "work"
    assert any(item["content"] == "今天下午开会" for item in briefing["groups"][0]["chats"])
    assert any("群聊原文" in item for item in briefing["task"])


def test_llm_briefing_keeps_raw_materials():
    persons = [
        _person(),
        _person(id_card="2", name="李四", wechat_id="wxid_lisi", remark="周凯的妻子"),
    ]
    briefing = build_llm_briefing(persons, [])
    assert briefing["subjects"][0]["name"] == "周凯"
    assert briefing["related_persons"][0]["remark"] == "周凯的妻子"
    assert "先总结本人" in briefing["task"][0]
    assert any("群聊原文" in item for item in briefing["task"])
    chat_briefing = build_llm_briefing(persons, [], "你好")
    assert chat_briefing["subjects"][0]["name"] == "周凯"
    assert "只回答当前 question" in chat_briefing["task"][0]
    assert not any("先总结本人" in item for item in chat_briefing["task"])


def test_screen_wechat_friends_supports_multiple_accounts():
    zhou = _person(accounts=(
        WeChatAccount(wechat_id="wxid_zhou_a", wechat_alias="zhoukai01"),
        WeChatAccount(wechat_id="wxid_zhou_b", wechat_alias="zhoukai02"),
    ))
    zhang = _person(id_card="2", name="张三", wechat_id="wxid_zhang", wechat_alias="zhangsan01")
    li = _person(id_card="3", name="李四", wechat_id="wxid_li")
    friends = [
        {"owner_name": "周凯", "owner_wechat_id": "wxid_zhou_a", "friend_nickname": "阿三", "friend_wechat_id": "wxid_zhang", "friend_alias": "zhangsan01", "friend_original_wechat_id": None, "friend_remark": "张三", "owner_alias": None},
        {"owner_name": "周凯", "owner_wechat_id": "wxid_zhou_b", "friend_nickname": "王五", "friend_wechat_id": "wxid_wang", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
        {"owner_name": "张三", "owner_wechat_id": "wxid_zhang", "friend_nickname": "凯子", "friend_wechat_id": "wxid_zhou_b", "friend_alias": "zhoukai02", "friend_original_wechat_id": None, "friend_remark": "周凯", "owner_alias": None},
        {"owner_name": "张三", "owner_wechat_id": "wxid_zhang", "friend_nickname": "王五", "friend_wechat_id": "wxid_wang", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
        {"owner_name": "李四", "owner_wechat_id": "wxid_li", "friend_nickname": "王五", "friend_wechat_id": "wxid_wang", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
    ]
    two = screen_wechat_friends([zhou, zhang, li], friends, ["周凯", "张三"])
    assert two["pairs"][0]["mutual"] is True
    assert [item["wechat_id"] for item in two["common_friends"]] == ["wxid_wang"]
    three = screen_wechat_friends([zhou, zhang, li], friends, ["周凯", "张三", "李四"])
    assert [item["wechat_id"] for item in three["common_friends"]] == ["wxid_wang"]
    assert three["common_friends"][0]["shared_count"] == 3
    assert any(pair["relation"] == "未发现好友关系" for pair in three["pairs"] if {pair["left"], pair["right"]} == {"周凯", "李四"})


def test_screen_wechat_friends_keeps_friends_shared_by_two_of_three():
    zhou = _person()
    zhang = _person(id_card="2", name="张三", wechat_id="wxid_zhang")
    li = _person(id_card="3", name="李四", wechat_id="wxid_li")
    friends = [
        {"owner_name": "周凯", "owner_wechat_id": "wxid_zhou", "friend_nickname": "王五", "friend_wechat_id": "wxid_wang", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
        {"owner_name": "张三", "owner_wechat_id": "wxid_zhang", "friend_nickname": "王五", "friend_wechat_id": "wxid_wang", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
        {"owner_name": "李四", "owner_wechat_id": "wxid_li", "friend_nickname": "王五", "friend_wechat_id": "wxid_wang", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
        {"owner_name": "周凯", "owner_wechat_id": "wxid_zhou", "friend_nickname": "赵六", "friend_wechat_id": "wxid_zhao", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
        {"owner_name": "张三", "owner_wechat_id": "wxid_zhang", "friend_nickname": "赵六", "friend_wechat_id": "wxid_zhao", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
        {"owner_name": "周凯", "owner_wechat_id": "wxid_zhou", "friend_nickname": "独有", "friend_wechat_id": "wxid_only", "friend_alias": None, "friend_original_wechat_id": None, "friend_remark": None, "owner_alias": None},
    ]
    screened = screen_wechat_friends([zhou, zhang, li], friends, ["周凯", "张三", "李四"])
    by_id = {item["wechat_id"]: item for item in screened["common_friends"]}
    assert set(by_id) == {"wxid_wang", "wxid_zhao"}
    assert by_id["wxid_wang"]["shared_count"] == 3
    assert by_id["wxid_zhao"]["shared_count"] == 2
    assert set(by_id["wxid_zhao"]["shared_by"]) == {"周凯", "张三"}


def _friend(owner, wechat_id=None, alias=None, original=None, nickname="某人"):
    return {
        "owner_name": owner,
        "owner_wechat_id": None,
        "owner_alias": None,
        "friend_nickname": nickname,
        "friend_remark": None,
        "friend_wechat_id": wechat_id,
        "friend_alias": alias,
        "friend_original_wechat_id": original,
    }


def test_screen_matches_same_wechat_field_and_skips_blank_fields():
    gao = _person(id_card="1", name="高磊", wechat_id="gaolei98766877")
    jia = _person(id_card="2", name="佳宁", wechat_id="wxid_7k2m3n8p")
    friends = [
        _friend("高磊", wechat_id="wxid_same", alias="acct_same", original="orig_same", nickname="甲"),
        _friend("佳宁", wechat_id="wxid_other", alias="acct_same", original="orig_other", nickname="乙"),
        _friend("高磊", wechat_id="wxid_only_id", nickname="丙"),
        _friend("佳宁", original="wxid_only_id", nickname="丁"),
        _friend("高磊", original="orig_only", nickname="戊"),
        _friend("佳宁", original="orig_only", nickname="己"),
        _friend("高磊", wechat_id="wxid_left", alias="foo", nickname="庚"),
        _friend("佳宁", wechat_id="wxid_right", alias="wxid_left", nickname="庚"),
        _friend("高磊", nickname="同名不同号"),
        _friend("佳宁", wechat_id="wxid_named", nickname="同名不同号"),
    ]
    screened = screen_wechat_friends([gao, jia], friends, ["高磊", "佳宁"])
    hits = screened["common_friends"]
    by_alias = {item["wechat_alias"]: item for item in hits if item["wechat_alias"]}
    assert set(by_alias["acct_same"]["wechat_id"].split("、")) == {"wxid_same", "wxid_other"}
    assert set(by_alias["acct_same"]["shared_by"]) == {"高磊", "佳宁"}
    assert any(item["original_wechat_id"] == "orig_only" for item in hits)
    assert not any(item["wechat_id"] == "wxid_only_id" or item["original_wechat_id"] == "wxid_only_id" for item in hits)
    assert not any(item["wechat_id"] in {"wxid_left", "wxid_right"} or item["wechat_alias"] in {"foo", "wxid_left"} for item in hits)
    assert not any(item["wechat_id"] == "wxid_named" for item in hits)

