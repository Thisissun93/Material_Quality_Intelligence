"""Write docs/stats_validation.md: every checked statistic next to an independently computed value.

    python scripts/validate_stats.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mqi import demo, engine, validation  # noqa: E402


def main(out: Path = ROOT / "docs" / "stats_validation.md") -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        dbpath = Path(tmp) / "validate.sqlite"
        demo.build(Path(tmp) / "inbox", dbpath)
        df = validation.run(engine.run(dbpath))
    n_ok = int(df["ok"].sum())
    text = "\n".join([
        "# 통계 기능 검증표", "",
        "앱의 계산 결과를 **앱 코드를 쓰지 않는 독립 계산**과 나란히 놓았습니다. 데이터는 데모 공장 "
        "(`scripts/export_for_jmp.py`가 JMP용으로 내보내는 것과 같은 데이터)이고, 허용 오차는 상대 1e-9입니다(정규 꼬리확률 ppm은 1e-7, T²는 1e-8). "
        "다시 만들려면 `python scripts/validate_stats.py`, 같은 검사가 `tests/test_validation.py`로 커밋마다 실행됩니다.", "",
        f"**{n_ok} / {len(df)} 항목 일치**", "",
        validation.markdown(df), "",
        "## 읽는 법", "",
        "- Gage R&R은 앱이 직접 계산한 제곱합 대신 statsmodels의 2원 ANOVA 결과로 분산 성분을 다시 구했습니다.",
        "- 공정능력의 군내 σ는 JMP 개별값 기본값과 같은 이동범위 평균 / d2(1.128)입니다.",
        "- 'EWMA 최대 편차'처럼 0과 비교하는 행은 전 로트의 차이 중 가장 큰 값입니다.",
        "- Box-Cox λ는 앱 화면에 소수 둘째 자리까지 표시하므로, 독립 계산도 같은 자리수로 반올림해 비교했습니다.", "",
        "## JMP 대조", "",
        "같은 데이터를 JMP로 직접 계산해 비교하는 절차는 [jmp_validation.md](jmp_validation.md)에 있습니다. "
        "JMP 결과 화면은 확인 후 이 문서에 추가합니다.", ""])
    out.write_text(text, encoding="utf-8")
    print(text)
    return bool(df["ok"].all())


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
