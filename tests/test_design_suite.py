from openroad_platform_analysis import list_design_packages, load_design_package


def test_v2_fixed_suite_has_required_versioned_oracles():
    packages = list_design_packages()
    assert [item["name"] for item in packages] == ["gcd", "fifo", "uart_tx", "ibex_alu"]
    for item in packages:
        package = load_design_package(item["name"])
        assert len(package["hashes"]["spec"]) == 64
        assert "module " in package["contents"]["golden_rtl"]
        assert "PASS" in package["contents"]["testbench"]
        # RTLScout's independent simulation judge counts checks from this
        # machine-readable line; plain PASS must never silently become an
        # uncounted oracle result.
        assert "TB_SUMMARY total=" in package["contents"]["testbench"]
