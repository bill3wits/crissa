"""The camp on her boot loop is soft (the User 2026-10-27, the crissatest walk): a pack boots, lands after Setup and
returns from the phone room camped on her boot loop, and nothing in a pack cleared it, so she looped her listening loop
for good. The wrap branch lifts THAT camp when the bridge says no session is open; every other camp stays hard.
The prefill builds her boot loop first. Pins on the source: the branch is inside Playhead._advance, which no test harness
drives, so the live proof is the player's own log line (`camp: <loop> lifted at a quiet wrap`)."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "scripts" / "house-conductor.py").read_text()


def test_the_lift_is_one_branch_keyed_on_her_boot_loop_and_the_bridge():
    body = SRC.split("if self.seq.camp:\n", 1)[1]
    assert SRC.count("lifted at a quiet wrap (no session open): heads down\"\n                              % self.seq.camp") == 1
    head = body.split("# A VISIT WITH NOTHING TO DO IS OVER.", 1)[0]
    assert "self.seq.camp == _BOOT_CAMP[\"key\"]" in head, "only the boot loop's camp"
    assert "not house_in_conversation()" in head, "a session open holds her"
    assert "not IS_BENCH" in head, "a bench never lifts"
    assert "self.seq.camp = None" in head
    assert "if self.key != self.seq.camp:\n                            self.play(self.seq.camp)\n                        return" in head, \
        "every other camp stays hard"


def test_the_kill_and_the_key():
    assert 'os.environ.get("CRISSA_BOOT_CAMP_LIFT", "1")' in SRC
    assert SRC.count('_BOOT_CAMP["key"] = boot') == 1, "set once, where the boot loop is chosen"


def test_the_prefill_builds_her_boot_loop_first():
    i = SRC.index("def mouth_patch_prefill_main")
    tail = SRC[i:]
    assert 'first = [k for k in (boot, getattr(graph, "listen_loop", None), graph.rest_for("down")) if k]' in tail
