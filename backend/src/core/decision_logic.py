# Regime: regime_status,
canh_bao_som: "có" if early_warning else "không",
entropy: round(entropy, 3) if entropy is not None else None,
so_tru_cau_truc: so_tru_ok,
},
}

# ---- Bước 3: Tự đánh giá độ tin cậy (dùng chung ảnh chụp) ----
try:
    from src.engine.confidence_layer import đánh_giá_độ_tin_cậy
    đg = đánh_giá_độ_tin_cậy(anh_chup=anh_chup, điểm_thị_trường_thật=diem_thị_trường_thật, độ_méo_chỉ_số=độ_méo_chỉ_số, mức_tập_trung=mức_tập_trung)
    ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"] = {
        "điểm_số": đg["điểm_tin_cậy"],
        "mức": đg["mức_đánh_giá"],
        "tạm_ngưng": đg["tạm_ngưng_kết_luận"],
        "lý_do_tạm_ngưng": đg["lý_do_tạm_ngưng"],
    }
except Exception:
    ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"] = {
        "điểm_số": 0.5, "mức": "TRUNG_BINH", "tạm_ngưng": False,
    }

# ---- Bước 4: Lớp bảo vệ quyết định (Guard) ----
try:
    from src.engine.decision_guard import kiem_tra_an_toan
    guarded = kiem_tra_an_toan(
        quyet_dinh_de_xuat=ket_qua["quyet_dinh"],
        ly_do_de_xuat=ket_qua["ly_do"],
        do_tin_cay=ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"],
        anh_chup=anh_chup,
        index_reality=ir,
    )

    if ir.get("do_lech_pha") == "MANH_GIA_TAO":
        guarded.force = "DỪNG NGOÀI"

    ket_qua["quyet_dinh"] = guarded["quyet_dinh"]
    ket_qua["ly_do"] = guarded["ly_do"]
    ket_qua["bi_chặn_bởi_bảo_vệ"] = guarded["bi_chặn"]
    ket_qua["lý_do_chặn"] = guarded["ly_do_chặn"]
except Exception:
    ket_qua["bi_chặn_bởi_bảo_vệ"] = False
    ket_qua["lý_do_chặn"] = None

# ---- Lưu file ----
out_dir = Path(src.config.DATA_DIR) / "output"
out_dir.mkdir(parents=True, exist_ok=True)
with open(out_dir / "final_decision.json", "w", encoding="utf-8") as f:
    json.dump(ket_qua, f, indent=2, ensure_ascii=False)


def in_bao_cao(kq: dict):
    icons = {"THAM GIA": "🟢", "QUAN SAT": "🟡", "GIAM RUI RO": "🟠", "DUNG NGOAI": "🔴"}
    icon = icons.get(kq.get("quyet_dinh", ""), "⚪")
    print("\n" + "=" * 60)
    print("  BỘ QUYẾT ĐỊNH CUỐI CÙNG")
    print("=" * 60)
    print(f"  {icon} Quyết định: {kq.get('quyet_dinh', 'N/A')}")
    if kq.get("bi_chặn_bởi_bảo_vệ"):
        print(f"      ↳ Bị chặn bởi lớp bảo vệ")
    print()
    for i, ld in enumerate(kq.get("ly_do", []), 1):
        print(f"    {i}. {ld}")
    print()
    ct = kq.get("chi_tiet", {})
    print(f"  Cấu trúc:     {ct.get('cau_truc', 'N/A')} ({ct.get('so_tru_cau_truc', '?')}/3 trụ)")
    print(f"  Regime:       {ct.get('regime', 'N/A')}")
    print(f"  Cảnh báo sớm: {ct.get('canh_bao_som', 'N/A')}")
    print(f"  Entropy:      {ct.get('entropy', 'N/A')}")
    đg = kq.get("độ_tin_cậy_sau_hiệu_chỉnh", {})
    if đg:
        icons_dg = {"CAO": "🟢", "TRUNG_BINH": "🟡", "THAP": "🔴"}
        icon_dg = icons_dg.get(đg.get("mức", ""), "⚪")
        print(f"  Tin cậy:      {icon_dg} {đg.get('điểm_số', 0):.0%} ({đg.get('mức', 'N/A')})")
        if đg.get("tạm_ngưng"):
            print(f"  ⚠ Tạm ngưng kết luận: {đg.get('lý_do_tạm_ngưng', '')}")
    print("=" * 60)


if __name__ == "__main__":
    kq = quyet_dinh_cuoi()
    in_bao_cao(kq)