"""Write JMP-ready CSV files and the values this app computes, so results can be checked side by side.

    python scripts/export_for_jmp.py      -> data/jmp/*.csv and data/jmp/expected.md
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mqi import config, db, engine  # noqa: E402

out = config.DATA_DIR / "jmp"
out.mkdir(parents=True, exist_ok=True)
b = engine.run()
db.read("SELECT part, operator, replicate, value FROM msa").to_csv(out / "gage_rr.csv", index=False)
b.product.to_csv(out / "product_lots.csv", index=False)

spec = config.specs()["product"]["properties"]
L = ["# Values to compare with JMP", "", "## Gage R&R (crossed, with interaction, K = 6)", "",
     "| Source | VarComp | %StudyVar | %Tolerance |", "|---|---|---|---|"]
for _, r in b.gage.components.iterrows():
    L.append(f"| {r['Source'].strip()} | {r['VarComp']:.2f} | {r['%StudyVar']:.2f} | {r.get('%Tolerance', float('nan')):.2f} |")
L += ["", f"ndc = {b.gage.ndc}", "", "## Capability (individuals)", "",
      "| Property | LSL | USL | Mean | σ within (MR̄/d2) | σ overall | Cpk | Ppk |", "|---|---|---|---|---|---|---|---|"]
for p, c in b.cap.items():
    L.append(f"| {p} | {spec[p].get('lsl')} | {spec[p].get('usl')} | {c.mean:.3f} | {c.sd_within:.3f} | "
             f"{c.sd_overall:.3f} | {c.cpk:.3f} | {c.ppk:.3f} |")
(out / "expected.md").write_text("\n".join(L), encoding="utf-8")
print("\n".join(L))
