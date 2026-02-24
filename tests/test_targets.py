"""Tests for targets module."""

from winactions.targets import TargetKind, TargetInfo, TargetRegistry


def test_target_info_creation():
    t = TargetInfo(kind=TargetKind.CONTROL, name="Save", type="Button")
    assert t.kind == TargetKind.CONTROL
    assert t.name == "Save"
    assert t.type == "Button"
    assert t.id is None
    assert t.rect is None


def test_target_info_with_rect():
    t = TargetInfo(
        kind=TargetKind.WINDOW,
        name="Notepad",
        id="1",
        rect=[100, 200, 800, 600],
    )
    assert t.rect == [100, 200, 800, 600]
    assert t.id == "1"


def test_registry_register_and_get():
    registry = TargetRegistry()
    t1 = TargetInfo(kind=TargetKind.CONTROL, name="Button1")
    t2 = TargetInfo(kind=TargetKind.CONTROL, name="Button2")

    registered = registry.register([t1, t2])
    assert len(registered) == 2
    assert t1.id == "1"
    assert t2.id == "2"

    assert registry.get("1") is t1
    assert registry.get("2") is t2
    assert registry.get("3") is None


def test_registry_find_by_name():
    registry = TargetRegistry()
    t = TargetInfo(kind=TargetKind.CONTROL, name="Save")
    registry.register(t)

    found = registry.find_by_name("Save")
    assert len(found) == 1
    assert found[0].name == "Save"


def test_registry_clear():
    registry = TargetRegistry()
    registry.register(TargetInfo(kind=TargetKind.CONTROL, name="X"))
    assert len(registry.all_targets()) == 1
    registry.clear()
    assert len(registry.all_targets()) == 0


def test_registry_to_list():
    registry = TargetRegistry()
    registry.register(
        TargetInfo(kind=TargetKind.CONTROL, name="OK", type="Button")
    )
    lst = registry.to_list(keep_keys=["name", "type"])
    assert len(lst) == 1
    assert lst[0]["name"] == "OK"
    assert lst[0]["type"] == "Button"
    assert "id" not in lst[0]
