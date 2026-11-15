from ferrule.hooks import STOP, HookRegistry, normalize


def test_normalize():
    assert normalize("BeforeSubmit") == "before_submit"
    assert normalize("before-submit") == "before_submit"
    assert normalize("after.transition") == "after_transition"


def test_priority_then_registration_order():
    h = HookRegistry()
    h.register("x", lambda: 1, priority=10, name="a")
    h.register("x", lambda: 2, priority=20, name="b")
    h.register("x", lambda: 3, priority=10, name="c")
    assert h.callbacks("x") == ["b", "a", "c"]


def test_stop():
    h = HookRegistry()
    h.register("x", lambda: STOP, priority=10, name="stopper")
    h.register("x", lambda: 1, priority=0, name="never")
    assert h.fire("x") == []
