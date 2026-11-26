from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parents[1] / "app.py"


def _board() -> str:
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not app.exception
    return next(item.value for item in app.markdown if 'class="board"' in item.value)


def test_overview_simulates_the_whole_directory_based_scenario_pack():
    board = _board()
    for sid in ("S01", "S02", "S03", "S04", "S05"):
        assert f'<td class="sid">{sid}</td>' in board
    assert "Aperçu indisponible" not in board


def test_overview_shows_each_scenario_score_and_failed_rules():
    board = _board()
    rows = {row.split("</td>")[0].split(">")[-1]: row for row in board.split("<tr>")[2:]}
    assert "0/100" in rows["S01"] and "aucune" in rows["S01"]
    assert "20/100" in rows["S02"] and "RULE-RES-001" in rows["S02"]
    assert "20/100" in rows["S03"] and "RULE-NET-004" in rows["S03"]
    assert "30/100" in rows["S04"] and "RULE-NET-003" in rows["S04"]
    assert "11/100" in rows["S05"] and "RULE-RES-003" in rows["S05"]
