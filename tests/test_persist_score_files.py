"""The persistence draw file, written once, and the freeze pins (Step 10, ticket 06)."""
import pytest

from persist.config import PersistConfig
from persist.prompt import load_prompt as load_persist_prompt
from persist.sample import PersistDraw
from persist.score_files import current_pins, read_draw, write_draw
from resolve.labels import LabelFileError
from resolve.sample import DrawnPair, Stratum

PAIRS = (DrawnPair("k:e1", "q:l1", "RiskFactor", Stratum.CANDIDATE),
         DrawnPair("k:e2", "q:l2", "RiskFactor", Stratum.REST))
DRAW = PersistDraw(config_hash="h1", populations={Stratum.CANDIDATE: 5, Stratum.NEAR: 0,
                                                  Stratum.REST: 9}, pairs=PAIRS)


def test_a_draw_file_reads_back_as_written(tmp_path):
    path = tmp_path / "scoring-draw.json"
    assert write_draw(path, DRAW, {"commit": "abc"}) is True
    assert read_draw(path) == DRAW


def test_the_same_draw_again_writes_nothing(tmp_path):
    path = tmp_path / "scoring-draw.json"
    write_draw(path, DRAW, {"commit": "abc"})
    assert write_draw(path, DRAW, {"commit": "def"}) is False
    assert '"abc"' in path.read_text(encoding="utf-8")


def test_another_draw_is_refused(tmp_path):
    path = tmp_path / "scoring-draw.json"
    write_draw(path, DRAW, {})
    with pytest.raises(LabelFileError, match="another draw"):
        write_draw(path, PersistDraw(config_hash="h2", populations=DRAW.populations,
                                     pairs=PAIRS), {})


def test_the_pins_name_both_prompts_and_both_models_with_effort():
    pins = current_pins(load_persist_prompt().prompt_version)
    assert pins["persist"].startswith("persist/v1@")
    assert pins["label"].startswith("persist_label/v1@")
    assert pins["persist_model"] == "claude-haiku-5-5@high"
    assert pins["label_model"] == "gpt-6-luna@high"


def test_the_config_hash_is_the_persistence_config():
    assert PersistConfig().config_hash != PersistConfig(top_k=4).config_hash


def test_the_pins_hold_the_draw_settings_the_config_hash_lacks():
    draw = current_pins(load_persist_prompt().prompt_version)["draw"]
    assert draw == "seed=20261014;near_rank_factor=2;candidate=60/near=25/rest=15"
