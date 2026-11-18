"""Statistics validation table: each app result next to a value computed a different way.

The reference side never calls the app's functions. It uses other libraries (statsmodels ANOVA,
pandas ewm, scipy distance) or a separate, plainly written implementation of the textbook formula,
on the same demo data that scripts/export_for_jmp.py exports for the JMP comparison.

    python scripts/validate_stats.py       -> docs/stats_validation.md
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial.distance import mahalanobis
import statsmodels.api as sm
from statsmodels.formula.api import ols

from . import config
from .analytics import capability, msa, spc

TOL = 1e-9


def _row(area, item, app, ref, method, tol=TOL):
    app, ref = float(app), float(ref)
    diff = abs(app - ref)
    ok = diff <= tol * max(1.0, abs(ref))
    return dict(area=area, item=item, app=app, reference=ref, diff=diff, ok=ok, method=method)


def _gage(b) -> list[dict]:
    d = b.gage_data[["part", "operator", "value"]].dropna().copy()
    d["value"] = d["value"].astype(float)
    spec = config.specs()["product"]["properties"]["viscosity"]
    tol = spec["usl"] - spec["lsl"]
    g = msa.gage_rr(d, tolerance=tol)
    a = sm.stats.anova_lm(ols("value ~ C(part) * C(operator)", d).fit(), typ=2)
    ms = (a["sum_sq"] / a["df"])
    p, o = d["part"].nunique(), d["operator"].nunique()
    r = d.groupby(["part", "operator"]).size().min()
    ms_p, ms_o, ms_po, ms_e = (ms["C(part)"], ms["C(operator)"], ms["C(part):C(operator)"], ms["Residual"])
    v_rep = ms_e
    v_int = max(0.0, (ms_po - ms_e) / r)
    v_op = max(0.0, (ms_o - ms_po) / (p * r))
    v_part = max(0.0, (ms_p - ms_po) / (o * r))
    v_grr = v_rep + v_op + v_int
    v_tot = v_grr + v_part
    c = g.components.set_index(g.components["Source"].str.strip())
    m = "statsmodels 2원 ANOVA 평균제곱 → 기대평균제곱(EMS) 분산 성분"
    return [
        _row("Gage R&R", "반복성 분산", c.loc["Repeatability", "VarComp"], v_rep, m),
        _row("Gage R&R", "측정자 분산", c.loc["Operator", "VarComp"], v_op, m),
        _row("Gage R&R", "부품×측정자 분산", c.loc["Part×Operator", "VarComp"], v_int, m),
        _row("Gage R&R", "부품 분산", c.loc["Part-to-Part", "VarComp"], v_part, m),
        _row("Gage R&R", "%StudyVar (Gage R&R)", g.grr_pct_sv, 100 * math.sqrt(v_grr / v_tot), m),
        _row("Gage R&R", "%Tolerance (K=6)", g.grr_pct_tol, 100 * 6 * math.sqrt(v_grr) / tol, m),
        _row("Gage R&R", "ndc", g.ndc, math.floor(1.41 * math.sqrt(v_part / v_grr)), m),
    ]


def _capability(b) -> list[dict]:
    out = []
    spec = config.specs()["product"]["properties"]
    for prop in ("viscosity", "tg"):
        s = spec[prop]
        lsl, usl = s.get("lsl"), s.get("usl")
        x = pd.Series(b.product[prop], dtype=float).dropna()
        c = capability.analyze(x.values, lsl, usl)
        sd_w = x.diff().abs().mean() / 1.128
        sd_o = x.std(ddof=1)
        mu = x.mean()
        k = lambda sd: min(v for v in ((usl - mu) / (3 * sd) if usl is not None else None,  # noqa: E731
                                       (mu - lsl) / (3 * sd) if lsl is not None else None) if v is not None)
        n = len(x)
        se = math.sqrt(1 / (9 * n) + k(sd_w) ** 2 / (2 * (n - 1)))
        tail = lambda z: 0.5 * math.erfc(z / math.sqrt(2))  # noqa: E731
        ppm = 1e6 * ((tail((usl - mu) / sd_o) if usl is not None else 0) +
                     (tail((mu - lsl) / sd_o) if lsl is not None else 0))
        lab = "점도" if prop == "viscosity" else "Tg"
        mp = "pandas 이동범위 평균 / d2(1.128), 표본 표준편차"
        out += [
            _row("공정능력", f"{lab} σ 군내 (MR̄/d2)", c.sd_within, sd_w, mp),
            _row("공정능력", f"{lab} Cpk", c.cpk, k(sd_w), mp),
            _row("공정능력", f"{lab} Ppk", c.ppk, k(sd_o), mp),
            _row("공정능력", f"{lab} Cpk 95% 하한 (Bissell)", c.cpk_ci[0], k(sd_w) - 1.96 * se, "Bissell(1990) 식 직접 계산"),
            _row("공정능력", f"{lab} 예상 불량 ppm", c.ppm, ppm, "math.erfc 정규 꼬리확률", tol=1e-7),
        ]
    # Box-Cox: maximum-likelihood λ by a plain grid search on the log-likelihood, on skewed data
    rng = np.random.default_rng(7)
    x = rng.lognormal(3.0, 0.35, 400)
    c = capability.analyze(x, None, 60.0)
    lam_app = float(c.method.split("λ=")[1].rstrip(")")) if "λ=" in c.method else float("nan")
    grid = np.linspace(-2, 2, 40001)
    logx, n = np.log(x), len(x)

    def llf(lam):
        y = logx if abs(lam) < 1e-12 else (x ** lam - 1) / lam
        return (lam - 1) * logx.sum() - n / 2 * math.log(y.var())

    lam_ref = grid[int(np.argmax([llf(v) for v in grid]))]
    out.append(_row("공정능력", "Box-Cox λ (비정규 데이터)", lam_app, round(lam_ref, 2),
                    "로그우도 격자 탐색 (0.0001 간격, 앱 표시 자리수로 반올림)", tol=0.0))
    return out


def _spc(b) -> list[dict]:
    cfg = config.rules()["spc"]
    x = pd.Series(b.product["viscosity"], dtype=float).reset_index(drop=True)
    base = b.baseline
    lim = spc.baseline_limits(x.values, base, cfg.get("sigma", "overall"))
    center = x.iloc[:base].mean()
    if cfg.get("sigma", "overall") == "moving_range":
        sigma = x.iloc[:base].diff().abs().mean() / 1.128
    else:
        sigma = x.iloc[:base].std(ddof=1)
    lam, L = cfg.get("ewma_lambda", 0.2), cfg.get("ewma_L", 2.7)
    ew = spc.ewma(x.values, lim, lam, L)
    ref = pd.concat([pd.Series([center]), x]).ewm(alpha=lam, adjust=False).mean().iloc[1:].reset_index(drop=True)
    t = np.arange(1, len(x) + 1)
    w = L * sigma * np.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * t)))
    k, h = cfg.get("cusum_k", 0.5), cfg.get("cusum_h", 4.0)
    cu = spc.cusum(x.values, lim, k, h)
    z = (x - center) / sigma
    hi, lo = [0.0], [0.0]
    for v in z:
        hi.append(max(0.0, hi[-1] + v - k))
        lo.append(max(0.0, lo[-1] - v - k))
    nl = spc.nelson(x.values, lim, (1, 2))
    side = np.sign(z)
    r2 = (side.rolling(9).apply(lambda s: abs(s.sum()) == 9, raw=True) == 1).fillna(False)
    m = ("기준 구간 평균·이동범위 σ(MR̄/1.128)" if cfg.get("sigma", "overall") == "moving_range"
         else "기준 구간 평균·표본 표준편차")
    return [
        _row("SPC", "관리 중심선", lim.center, center, m),
        _row("SPC", "관리 σ", lim.sigma, sigma, m),
        _row("SPC", "EWMA 최대 편차 (전 로트)", np.abs(ew["ewma"] - ref).max(), 0.0,
             "pandas ewm(adjust=False), 중심선에서 시작"),
        _row("SPC", "EWMA 관리한계 최대 편차", np.abs(ew["ucl"] - (center + w)).max(), 0.0,
             "시점별 한계 식 L·σ·√(λ/(2−λ)·(1−(1−λ)^2t))"),
        _row("SPC", "CUSUM C⁺ 최대 편차", np.abs(cu["cusum_hi"].values - np.array(hi[1:])).max(), 0.0,
             "표 방식 CUSUM 별도 구현"),
        _row("SPC", "CUSUM C⁻ 최대 편차", np.abs(cu["cusum_lo"].values - np.array(lo[1:])).max(), 0.0,
             "표 방식 CUSUM 별도 구현"),
        _row("SPC", "Nelson 1 (3σ 밖) 건수", int(nl[1].sum()), int((z.abs() > 3).sum()), "z 점수 직접 비교", tol=0.0),
        _row("SPC", "Nelson 2 (9점 한쪽) 건수", int(nl[2].sum()), int(r2.sum()), "pandas rolling 9점 부호", tol=0.0),
    ]


def _t2(b) -> list[dict]:
    """Checks the T² values the app actually produced (b.t2), in whichever reference mode is configured."""
    from .engine import T2_PROPS
    cfg = config.rules()["spc"]
    if b.t2 is None:
        return []
    props = [p for p in T2_PROPS if p in b.product]
    X = b.product[props].astype(float)
    window, alpha = cfg.get("t2_window", 30), cfg.get("t2_alpha", 0.01)
    Xv, base = X.values, b.baseline
    errs, m = [], None
    for i in range(len(X)):
        if window:
            if i < window:
                continue
            ref = Xv[i - window:i]
        else:
            ref = Xv[:base]
        m = len(ref)
        vi = np.linalg.inv(np.cov(ref, rowvar=False))
        errs.append(abs(mahalanobis(Xv[i], ref.mean(axis=0), vi) ** 2 - b.t2["t2"].iloc[i]))
    p = len(props)
    ucl = p * (m + 1) * (m - 1) / (m * (m - p)) * stats.f.ppf(1 - alpha, p, m - p)
    rel = max(errs) / max(1.0, float(np.nanmax(b.t2["t2"])))
    mode = f"직전 {window}개 로트 기준" if window else f"고정 기준 구간 {base}개 로트"
    return [
        _row("다변량", f"Hotelling T² 상대 최대 편차 ({len(errs)}개 로트, {mode})", rel, 0.0,
             "scipy 마할라노비스 거리², 역행렬 np.linalg.inv", tol=1e-8),
        _row("다변량", f"T² 관리한계 (α={alpha}, Phase II)", b.t2["ucl"].iloc[-1], ucl, "F 분포 식 직접 계산"),
    ]


def run(b) -> pd.DataFrame:
    """All checks on the analysed demo data (engine.Bundle)."""
    rows = _gage(b) + _capability(b) + _spc(b) + _t2(b)
    return pd.DataFrame(rows)


def markdown(df: pd.DataFrame) -> str:
    def f(v):
        if isinstance(v, (bool, np.bool_)):
            return "✅" if v else "❌"
        if isinstance(v, (int, np.integer)) or (isinstance(v, float) and v.is_integer() and abs(v) < 1e6):
            return f"{int(v)}"
        return f"{v:.6g}"

    L = ["| 영역 | 항목 | 앱 | 독립 계산 | 차이 | 일치 | 독립 계산 방법 |", "|---|---|---|---|---|---|---|"]
    for r in df.itertuples():
        L.append(f"| {r.area} | {r.item} | {f(r.app)} | {f(r.reference)} | {r.diff:.1e} | {f(r.ok)} | {r.method} |")
    return "\n".join(L)
