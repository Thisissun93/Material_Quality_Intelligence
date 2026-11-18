"""Time the 'one complaint/OOS lot investigation' scenario end to end on a fresh demo plant.

    python scripts/benchmark_investigation.py          -> docs/benchmark_investigation.md (+ .json)

Steps timed: building the analysis once (engine.run, what a shift does when new data arrives), then
collecting the investigation pack for three demo lots (customer complaint, OOS first result, FDC alarm),
each repeated and reported as the median. The manual baseline is the author's own experience, not a
measurement; see the report.
"""
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mqi import demo, engine, investigation, qms  # noqa: E402

REPEAT = 5


def main(out_dir: Path = ROOT / "docs") -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        dbpath = tmp / "bench.sqlite"
        demo.build(tmp / "inbox", dbpath)

        t = time.perf_counter()
        b = engine.run(dbpath)
        t_engine = time.perf_counter() - t

        complaint_lot = qms.complaints(dbpath).iloc[0]["lot_id"]
        cases = [
            ("고객 클레임", complaint_lot, None),
            ("최초 결과 OOS", demo.OOS_DEMO_LOT, "viscosity"),
            ("설비 FDC 알람", demo.FDC_DEMO_LOT, None),
        ]
        rows = []
        for title, lot, prop in cases:
            runs = [investigation.collect(b, lot, prop) for _ in range(REPEAT)]
            last = runs[-1]
            med = {k: statistics.median(r.seconds[k] for r in runs) for k in last.seconds}
            rows.append(dict(case=title, lot=lot, level=last.level, systems=last.systems,
                             n_systems=len(last.systems), n_records=last.n_records, records=last.records,
                             seconds=med, total=sum(med.values()),
                             same_raw_lots=int(last.data["same_raw"]["product_lot"].nunique())
                             if len(last.data["same_raw"]) else 0))
            if title == "고객 클레임":
                (out_dir / "investigation_example.md").write_text(investigation.markdown(last), encoding="utf-8")

    res = dict(engine_seconds=t_engine, repeat=REPEAT, python=platform.python_version(),
               machine=platform.machine(), cases=rows)
    (out_dir / "benchmark_investigation.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                                          encoding="utf-8")
    (out_dir / "benchmark_investigation.md").write_text(report(res), encoding="utf-8")
    return res


def report(res: dict) -> str:
    labels = {k: label for k, label, _ in investigation.SECTIONS}
    L = ["# 업무 시나리오 측정: 로트 1건 조사", "",
         "클레임이나 OOS가 들어왔을 때 품질 엔지니어가 한 로트에 대해 모으는 자료를, 앱이 한 번에 모으는 데 걸리는 "
         "시간을 쟀습니다. `python scripts/benchmark_investigation.py`로 다시 잴 수 있습니다.", "",
         "## 결과", "",
         "| 시나리오 | 로트 | 출하 판정 | 데이터를 가져온 시스템 | 모은 기록 | 수집 시간 (중앙값) |",
         "|---|---|---|---|---|---|"]
    for r in res["cases"]:
        L.append(f"| {r['case']} | {r['lot']} | {r['level']} | {r['n_systems']}곳 | {r['n_records']}건 | "
                 f"{r['total']:.2f}초 |")
    L += ["", f"- 분석 1회 실행 (`engine.run`, 새 데이터가 들어올 때 한 번): **{res['engine_seconds']:.1f}초**",
          f"- 로트별 수집은 {res['repeat']}회 반복의 중앙값, 8D 초안 작성 포함",
          f"- 환경: Python {res['python']}, {res['machine']}, SQLite, CPU만 사용", "",
          "## 수작업과 비교", "",
          "| 방식 | 로트 1건 자료 수집 | 근거 |", "|---|---|---|",
          "| 수작업 | 반나절 가까이 | 작성자가 FC-BGA 양산 라인에서 실제로 겪은 시간 (계측값 아님) |",
          f"| MQI | 분석 {res['engine_seconds']:.0f}초 + 로트당 약 {max(r['total'] for r in res['cases']):.2f}초 | 위 측정 |",
          "",
          "수작업 시간은 시스템마다 로그인해 조회하고, 결과를 엑셀로 옮겨 합치고, 같은 원료를 쓴 로트를 찾는 시간을 "
          "포함합니다. 비교는 '자료를 모으는 시간'에 한정됩니다. 원인 판단과 대책 결정은 여전히 사람이 합니다.", "",
          "## 앱이 모으는 항목 (고객 클레임 시나리오)", "",
          "| 항목 | 시스템 | 기록 | 시간 (ms) |", "|---|---|---|---|"]
    c = res["cases"][0]
    for k, label, systems in investigation.SECTIONS:
        L.append(f"| {labels[k]} | {', '.join(systems) or '앱이 생성'} | {c['records'].get(k, 0)} | "
                 f"{1000 * c['seconds'].get(k, 0):.1f} |")
    L += ["", f"같은 원료 로트를 쓴 다른 제품 로트 {c['same_raw_lots']}개가 함께 나와, 봉쇄·수평 전개 범위를 바로 정할 수 "
          "있습니다. 전체 패키지 예시: [investigation_example.md](investigation_example.md)", "",
          "## 한계", "",
          "- 데이터는 합성 데모 공장입니다. 실제 공장에서는 시스템 연동(MES·LIMS API) 응답 시간이 더해집니다.",
          "- 수작업 기준은 한 사람의 경험입니다. 실제 도입 전에는 현장에서 같은 시나리오를 수작업으로 재서 비교해야 합니다."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    r = main()
    print(report(r))
