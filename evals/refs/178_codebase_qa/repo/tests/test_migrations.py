from ferrule.migrations.runner import Migration, plan


def M(i, deps=(), prio=0, repl=()):
    return Migration(i, i, list(deps), prio, list(repl))


def test_linear():
    assert plan([M("a"), M("b", ["a"]), M("c", ["b"])], set()) == ["a", "b", "c"]


def test_priority_breaks_ties():
    assert plan([M("a"), M("b", ["a"], 1), M("c", ["a"])], set()) == ["a", "c", "b"]


def test_squash_used_on_fresh_db():
    migs = [M("a"), M("b", ["a"]), M("s", ["a"], repl=["b"]), M("c", ["b"])]
    assert plan(migs, set()) == ["a", "s", "c"]


def test_squash_skipped_when_partially_applied():
    migs = [M("a"), M("b", ["a"]), M("s", ["a"], repl=["b"]), M("c", ["b"])]
    assert plan(migs, {"a", "b"}) == ["c"]
