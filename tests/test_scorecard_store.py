"""Tests that Core's Scorecard round-trips through the corpus store."""

from __future__ import annotations

import json
from pathlib import Path

from verity_core.scorecard import AXES, Scorecard, scorecard_path

from verity_corpus.scorecard_store import exists, list_scored, load, save


def _card_with_zero_and_unscored(env_id: str = "env1") -> Scorecard:
    card = Scorecard(env_id=env_id)
    card.set_axis("V1", 0.0, "verity-signal", {"pass_rate": 0.0}, "measured zero")
    card.set_axis("U2", 0.75, "verity-redteam", {"exploits": 1})
    return card


class TestScorecardStore:
    def test_save_uses_core_path_layout(self, tmp_path: Path) -> None:
        card = _card_with_zero_and_unscored()
        path = save(card, tmp_path)
        assert path == scorecard_path(tmp_path, "env1")
        assert path.is_file()

    def test_round_trip_preserves_axis_values_including_none_vs_zero(
        self, tmp_path: Path
    ) -> None:
        original = _card_with_zero_and_unscored()
        save(original, tmp_path)
        loaded = load("env1", tmp_path)

        assert loaded.env_id == "env1"
        assert set(loaded.axes) == set(AXES)
        assert loaded.get_axis("V1").value == 0.0
        assert loaded.get_axis("U2").value == 0.75
        assert loaded.get_axis("V2").value is None
        assert loaded.get_axis("V1").tool == "verity-signal"
        assert loaded.get_axis("U2").evidence == {"exploits": 1}

        raw = json.loads((tmp_path / "env1.json").read_text(encoding="utf-8"))
        assert raw["axes"]["V1"]["value"] == 0.0
        assert raw["axes"]["V2"]["value"] is None

    def test_exists_and_list_scored(self, tmp_path: Path) -> None:
        assert exists("env1", tmp_path) is False
        assert list_scored(tmp_path) == []
        save(_card_with_zero_and_unscored("env1"), tmp_path)
        save(_card_with_zero_and_unscored("env2"), tmp_path)
        assert exists("env1", tmp_path) is True
        assert sorted(list_scored(tmp_path)) == ["env1", "env2"]

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(FileNotFoundError, match="no scorecard"):
            load("missing", tmp_path)
