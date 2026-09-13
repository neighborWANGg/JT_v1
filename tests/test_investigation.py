from app.investigation import (
    ChatItem,
    MysqlPerson,
    build_group_profiles,
    build_llm_briefing,
    classify_groups_heuristic,
    lexical_hit,
    summarize_persons,
)


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
