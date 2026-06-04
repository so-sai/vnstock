"""
report_renderer.py — Human-readable SRV report with VI/EN regime mapping.

Usage:
    from core.presentation.report_renderer import render_report
    text = render_report(srv_report, lang="vi")
"""

from core.validation.state_reconstruction_validator import SRVReport
from core.validation.state_space_validator import StateSpaceValidator
from core.presentation.vi_localizer import localize_regime, localize_trend_quality


REGIME_COLORS = {
    "CRISIS": "red",
    "RANGING": "yellow",
    "TRENDING": "green",
}


def regime_emoji(regime: str) -> str:
    mapping = {"CRISIS": "!!", "RANGING": "--", "TRENDING": "++"}
    return mapping.get(regime, "??")


def build_regime_timeline(sequence: list[str], width: int = 50) -> str:
    if not sequence:
        return "(empty)"
    step = max(1, len(sequence) // width)
    sampled = sequence[::step][:width]
    return "".join(regime_emoji(r) for r in sampled)


def render_report(report: SRVReport, lang: str = "vi", console_safe: bool = False) -> str:
    is_vi = lang == "vi"
    lines = []
    sep = "=" * 58

    lines.append(sep)
    if is_vi:
        lines.append(f"  BAO CAO TAI TAO TRANG THAI THI TRUONG")
    else:
        lines.append(f"  STATE RECONSTRUCTION REPORT")
    lines.append(f"  {report.contract_name}")
    lines.append(f"  {report.date_range} ({report.total_days_processed} ngay)")
    lines.append(sep)

    # Regime distribution
    lines.append("")
    if is_vi:
        lines.append(f"  PHAN BO REGIME:")
    else:
        lines.append(f"  REGIME DISTRIBUTION:")
    for r in ["CRISIS", "RANGING", "TRENDING"]:
        count = report.regime_type_summary.get(r, 0)
        pct = count / max(1, report.total_days_processed) * 100
        name = localize_regime(r) if is_vi else r
        bar = "#" * int(pct / 2)
        lines.append(f"    {name:15s} {count:4d} ngay ({pct:5.1f}%) {bar}")

    # Transition timeline
    lines.append("")
    if report.regime_sequence:
        if is_vi:
            lines.append(f"  DUONG THOI GIAN REGIME:")
        else:
            lines.append(f"  REGIME TIMELINE:")
        timeline = build_regime_timeline(report.regime_sequence)
        lines.append(f"    {timeline}")
        if is_vi:
            lines.append(f"    (!!=KHUNG_HOANG  --=DI_NGANG  ++=XU_HUONG)")
        else:
            lines.append(f"    (!!=CRISIS  --=RANGING  ++=TRENDING)")

    # State-space analysis
    if report.regime_sequence and len(report.regime_sequence) >= 2:
        v = StateSpaceValidator("report")
        analysis = v.analyze(report.regime_sequence)

        lines.append("")
        if is_vi:
            lines.append(f"  PHAN TICH KHONG GIAN TRANG THAI:")
        else:
            lines.append(f"  STATE-SPACE ANALYSIS:")
        lines.append(f"    Entropy:           {analysis['entropy']:.4f}")
        lines.append(f"    Persist ratio:     {analysis['persist_ratio']:.2%}")
        lines.append(f"    Chuyen doi regime: {analysis['num_transitions']} "
                     f"({analysis['num_transitions']/max(1,analysis['total_days'])*100:.1f}/100ngay)")

        lines.append("")
        if is_vi:
            lines.append(f"  MA TRAN CHUYEN DOI:")
        else:
            lines.append(f"  TRANSITION MATRIX P(next|current):")
        lines.append(f"    {'':>12s} {'CRISIS':>10s} {'RANGING':>10s} {'TRENDING':>10s}")
        for r1 in ["CRISIS", "RANGING", "TRENDING"]:
            vals = [analysis["transition_matrix"].get(r1, {}).get(r2, 0) for r2 in ["CRISIS", "RANGING", "TRENDING"]]
            name = localize_regime(r1) if is_vi else r1
            lines.append(f"    {name:>12s} {vals[0]:10.4f} {vals[1]:10.4f} {vals[2]:10.4f}")

        lines.append("")
        if is_vi:
            lines.append(f"  THOI GIAN DUY TRI REGIME (ngay):")
        else:
            lines.append(f"  DWELL TIME (consecutive days):")
        for r in ["CRISIS", "RANGING", "TRENDING"]:
            d = analysis["dwell_stats"].get(r, {})
            if d.get("count", 0) > 0:
                name = localize_regime(r) if is_vi else r
                lines.append(f"    {name:12s} count={d['count']:3d} "
                             f"trung binh={d['mean']:5.1f} "
                             f"trung vi={d['median']:4.1f} "
                             f"khoang={d['min']}-{d['max']}")

    # Suite A: DBE
    lines.append("")
    lines.append(sep)
    if is_vi:
        lines.append(f"  NGHI THUC A -- DO ON DINH DBE:")
    else:
        lines.append(f"  SUITE A -- DBE STABILITY:")
    lines.append(f"    Flip rate:     {report.suite_a.flip_rate:.2%}")
    lines.append(f"    Flip count:    {report.suite_a.flip_count}")
    lines.append(f"    Confidence:    {report.suite_a.mean_confidence:.4f}")
    lines.append(f"    Strength:      {report.suite_a.mean_strength:.4f}")

    # Suite B: DPL
    lines.append("")
    if is_vi:
        lines.append(f"  NGHI THUC B -- DO BEN DPL:")
    else:
        lines.append(f"  SUITE B -- DPL PERSISTENCE:")
    lines.append(f"    Persistent:    {report.suite_b.persistent_days}")
    lines.append(f"    Flickering:    {report.suite_b.flickering_days} "
                 f"({report.suite_b.flicker_pct:.2%})")
    lines.append(f"    Stability:     {report.suite_b.mean_stability:.4f}")

    # Suite C: TTL
    lines.append("")
    if is_vi:
        lines.append(f"  NGHI THUC C -- CHUYEN DOI TTL:")
    else:
        lines.append(f"  SUITE C -- TTL TRANSITION:")
    lines.append(f"    Hit rate:      {report.suite_c.hit_rate:.2%}")
    lines.append(f"    Hits/Events:   {report.suite_c.hits}/{report.suite_c.total_events}")
    lines.append(f"    Misses:        {report.suite_c.misses}")
    lines.append(f"    FP rate:       {report.suite_c.false_positive_rate:.2%}")
    lines.append(f"    Delay (days):  {report.suite_c.mean_delay_days:.1f}")
    lines.append(f"    TTL triggers:  {report.suite_c.total_ttl_triggers}")

    # Transition matches
    if report.suite_c.matches:
        lines.append("")
        if is_vi:
            lines.append(f"  SU KIEN CHUYEN DOI:")
        else:
            lines.append(f"  TRANSITION EVENTS:")
        for m in report.suite_c.matches:
            hit = "OK" if m.is_hit else "MISS"
            lines.append(f"    {hit} {m.event_name:25s} "
                         f"type={m.event_type:15s} "
                         f"delay={m.delay_days:2d}d "
                         f"ttl={m.ttl_date}")

    lines.append("")
    lines.append(sep)
    if is_vi:
        lines.append(f"  Bao cao hoan tat. {report.total_days_processed} ngay da xu ly.")
    else:
        lines.append(f"  Report complete. {report.total_days_processed} days processed.")
    lines.append(sep)

    return "\n".join(lines)


def print_report(report: SRVReport, lang: str = "vi"):
    print(render_report(report, lang))
