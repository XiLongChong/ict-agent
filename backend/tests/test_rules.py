"""风险规则回溯、组合命中与案件库幂等测试。"""

from pathlib import Path

from ict_agent.data import CaseStore, DuckDBStore, ReviewWrite
from ict_agent.rules import RuleThresholds, build_rule_scan


def _test_thresholds() -> RuleThresholds:
    return RuleThresholds(
        deep_overdue_amount=100,
        deep_overdue_days=90,
        overdue_growth_amount=50,
        stale_inventory_amount=100,
        stale_inventory_rate=0.3,
        inventory_buildup_amount=500,
        inventory_buildup_rate=0.5,
        inventory_slowdown_amount=500,
        inventory_sales_decline_rate=0.5,
        overdue_rate_threshold=0.7,
        unpaid_aging_days=90,
        unpaid_amount=50,
        zero_sales_inventory_amount=50,
        very_old_inventory_days=365,
        very_old_inventory_amount=50,
        extension_count_min=5,
        penalty_interest_amount=50,
        stale_ratio_threshold=0.5,
        stale_ratio_amount=50,
        borrow_overdue_days=60,
        overdue_stock_amount=50,
        return_ratio_threshold=0.15,
        return_amount=50,
        negative_payment_amount=50,
        negative_payment_ratio=0.15,
        aging_overdue_amount=50,
        negative_margin_loss=50,
        margin_gap=0.05,
        margin_actual_max=0.02,
        term_overage_days=120,
        term_overage_amount=50,
        credit_zero_recent_sales_ar=50,
        no_credit_min_ar=50,
    )


def test_rule_scan_detects_single_and_composite_risks(store: DuckDBStore) -> None:
    draft = build_rule_scan(store, _test_thresholds())
    rule_ids = {hit.rule_id for hit in draft.hits}

    assert draft.run.observation_date == "2026-07-31"
    assert draft.run.receivable_cases == 1
    assert draft.run.inventory_cases == 3
    assert "AR_OPERATING_DEEP_OVERDUE" in rule_ids
    assert "AR_OPERATING_EXPOSURE_BUILDUP" in rule_ids
    assert "AR_BLACKLIST_EXPOSURE" not in rule_ids
    assert "INV_MATERIAL_BUILDUP" in rule_ids
    assert "INV_STALE_NO_SALES" in rule_ids
    # 2026.08-v2 新增库存规则
    assert "INV_ZERO_SALES_STOCK" in rule_ids
    assert "INV_VERY_OLD_STOCK" in rule_ids
    assert "INV_OVERDUE_STOCK" in rule_ids
    assert "INV_STALE_RATIO_HIGH" in rule_ids
    # 同实体合并：C015 命中多条应收规则只生成一个案件
    ar_cases = [c for c in draft.cases if c.case_type == "ACCOUNTS_RECEIVABLE"]
    assert len(ar_cases) == 1
    assert ar_cases[0].entity_id == "C015"
    assert ar_cases[0].rule_hit_count == 2
    assert ar_cases[0].priority == "HIGH"


def test_v2_ar_rules_trigger_on_targeted_data(raw_data_dir: Path, database_path: Path) -> None:
    """用定向微型数据集验证新增应收规则 A1/A2/A3/A4 触发。"""

    from ict_agent.data import rebuild_database

    data_dir = raw_data_dir
    # 构造能触发各新应收规则的数据
    from tests.conftest import _write_csv

    _write_csv(
        data_dir / "销售流水.csv",
        [
            {
                "出库日期": "2026-03-01",
                "客户编号": "C010",
                "合同号": "",
                "销售订单号": "S99",
                "库存组织名称": "W1",
                "物料编码": "M9",
                "数量": 1,
                "出库类型": "销售出库",
                "事务处理类型名称": "正常销售",
                "销售金额_折扣后_含税": 500,
                "出库成本金额": 300,
            },
            {
                "出库日期": "2026-07-01",
                "客户编号": "C010",
                "合同号": "",
                "销售订单号": "S98",
                "库存组织名称": "W1",
                "物料编码": "M9",
                "数量": 1,
                "出库类型": "销售出库",
                "事务处理类型名称": "正常销售",
                "销售金额_折扣后_含税": 100,
                "出库成本金额": 60,
            },
        ],
    )
    _write_csv(
        data_dir / "业务回款明细.csv",
        [
            {
                "回款日期": "2026-07-20",
                "客户编号": "C010",
                "合同号": "",
                "销售订单号": "S98",
                "回款金额": 100,
                "超期利息金额": 0,
                "最终承诺还款日期": "2026-07-31",
                "是否超期": "N",
                "超期天数": 0,
                "回款账龄": 30,
                "物料编码": "M9",
            }
        ],
    )
    _write_csv(
        data_dir / "应收快照_月末24期.csv",
        [
            {
                "快照时间": "2026-07-31",
                "合同号": "",
                "客户编号": "C010",
                "客户名称": "测试客户十",
                "销售订单号": "S99",
                "应收金额": 500,
                "超期应收金额": 500,
                "超期30天以上金额": 500,
                "超期60天以上金额": 500,
                "最终承诺还款日期": "2026-01-31",
                "是否展期": "N",
                "超期天数": 180,
                "物料编码": "M9",
            }
        ],
    )
    _write_csv(
        data_dir / "客户授信.csv",
        [
            {
                "客户编号_中台": "C010",
                "客户名称": "测试客户十",
                "授信额度": 20,
                "黑白名单状态": 0,
                "黑白名单原因": "",
                "黑白名单创建时间": "2025-01-01",
                "失信分级": "一般",
                "净资产": 1000,
                "净利润": 100,
                "信用保险": "N",
            }
        ],
    )
    db = database_path
    rebuild_database(data_dir, db)
    store = DuckDBStore(db)
    draft = build_rule_scan(store, _test_thresholds())
    rule_ids = {hit.rule_id for hit in draft.hits}
    # 授信额度 20 万 x10000=200000 元，应收 500 元 < 授信，不触发 A3；A4 需授信=0
    # 触发：A1 高超期率(100%)、A2 长期未回款(S99 出库2026-03 超90天无回款)
    assert "AR_OVERDUE_RATE_HIGH" in rule_ids
    assert "AR_UNPAID_AGING" in rule_ids


def test_v2_extension_and_penalty_rules_trigger(raw_data_dir: Path, database_path: Path) -> None:
    """定向数据集验证 A5 多次展期、A6 高额罚息触发。"""

    from ict_agent.data import rebuild_database

    from tests.conftest import _write_csv

    data_dir = raw_data_dir
    _write_csv(
        data_dir / "展期记录.csv",
        [
            {
                "快照时间": f"2026-0{mo}-01",
                "合同号": "X1",
                "客户编号": "C020",
                "销售订单号": f"S{mo}",
                "物料编码": "M1",
                "最终承诺还款日期": "2026-07-31",
                "是否展期": "Y",
                "超期天数": 0,
                "gkey": f"g{mo}",
            }
            for mo in range(1, 7)
        ],
    )
    _write_csv(
        data_dir / "业务回款明细.csv",
        [
            {
                "回款日期": "2026-07-20",
                "客户编号": "C020",
                "合同号": "X1",
                "销售订单号": "S1",
                "回款金额": 100,
                "超期利息金额": 60,
                "最终承诺还款日期": "2026-06-30",
                "是否超期": "Y",
                "超期天数": 10,
                "回款账龄": 30,
                "物料编码": "M1",
            }
        ],
    )
    _write_csv(
        data_dir / "应收快照_月末24期.csv",
        [
            {
                "快照时间": "2026-07-31",
                "合同号": "",
                "客户编号": "C020",
                "客户名称": "测试客户二十",
                "销售订单号": "S1",
                "应收金额": 200,
                "超期应收金额": 0,
                "超期30天以上金额": 0,
                "超期60天以上金额": 0,
                "最终承诺还款日期": "2026-07-31",
                "是否展期": "N",
                "超期天数": 0,
                "物料编码": "M1",
            }
        ],
    )
    db = database_path
    rebuild_database(data_dir, db)
    store = DuckDBStore(db)
    draft = build_rule_scan(store, _test_thresholds())
    rule_ids = {hit.rule_id for hit in draft.hits}
    # 6 个 gkey >= 阈值 5 → A5；罚息 60 >= 阈值 50 → A6
    assert "AR_EXTENSION_ABUSE" in rule_ids
    assert "AR_PENALTY_INTEREST_HIGH" in rule_ids


def test_v3_sales_and_margin_rules_trigger(raw_data_dir: Path, database_path: Path) -> None:
    """定向数据集验证第三批 C1 异常退货、C3 负回款、D1 负毛利触发。"""

    from ict_agent.data import rebuild_database

    from tests.conftest import _write_csv

    data_dir = raw_data_dir
    _write_csv(
        data_dir / "销售流水.csv",
        [
            {
                "出库日期": "2026-06-10",
                "客户编号": "C030",
                "客户名称": "测试客户三十",
                "合同号": "",
                "销售订单号": "S1",
                "库存组织名称": "W1",
                "物料编码": "M1",
                "数量": 10,
                "出库类型": "销售出库",
                "事务处理类型名称": "正常销售",
                "销售金额_折扣后_含税": 1000,
                "出库成本金额": 700,
            },
            {
                "出库日期": "2026-07-12",
                "客户编号": "C030",
                "客户名称": "测试客户三十",
                "合同号": "",
                "销售订单号": "S2",
                "库存组织名称": "W1",
                "物料编码": "M1",
                "数量": -5,
                "出库类型": "销售退货",
                "事务处理类型名称": "退货",
                "销售金额_折扣后_含税": -500,
                "出库成本金额": -350,
            },
        ],
    )
    _write_csv(
        data_dir / "业务回款明细.csv",
        [
            {
                "回款日期": "2026-07-20",
                "客户编号": "C030",
                "客户名称": "测试客户三十",
                "合同号": "",
                "销售订单号": "S1",
                "回款金额": 200,
                "超期利息金额": 0,
                "最终承诺还款日期": "2026-07-31",
                "是否超期": "N",
                "超期天数": 0,
                "回款账龄": 30,
                "物料编码": "M1",
            },
            {
                "回款日期": "2026-07-21",
                "客户编号": "C030",
                "客户名称": "测试客户三十",
                "合同号": "",
                "销售订单号": "S1",
                "回款金额": -150,
                "超期利息金额": 0,
                "最终承诺还款日期": "2026-07-31",
                "是否超期": "N",
                "超期天数": 0,
                "回款账龄": 30,
                "物料编码": "M1",
            },
        ],
    )
    _write_csv(
        data_dir / "增值合同签约明细.csv",
        [
            {
                "申请日期": "2026-05-01",
                "合同编号": "Y1",
                "合同状态": "流程结束",
                "客户名称": "测试客户三十",
                "销售金额": 1000,
                "实估毛利率_不含税": 0.3,
                "实际净毛利率_不含税": -0.2,
                "合同文本账期": 60,
                "实际账期": 200,
                "开票金额1": 800,
            }
        ],
    )
    _write_csv(
        data_dir / "应收快照_月末24期.csv",
        [
            {
                "快照时间": "2026-07-31",
                "合同号": "",
                "客户编号": "C030",
                "客户名称": "测试客户三十",
                "销售订单号": "S1",
                "应收金额": 200,
                "超期应收金额": 0,
                "超期30天以上金额": 0,
                "超期60天以上金额": 0,
                "最终承诺还款日期": "2026-07-31",
                "是否展期": "N",
                "超期天数": 0,
                "物料编码": "M1",
            }
        ],
    )
    _write_csv(
        data_dir / "客户授信.csv",
        [
            {
                "客户编号_中台": "C030",
                "客户名称": "测试客户三十",
                "授信额度": 1000,
                "黑白名单状态": 0,
                "黑白名单原因": "",
                "黑白名单创建时间": "2025-01-01",
                "失信分级": "一般",
                "净资产": 1000,
                "净利润": 100,
                "信用保险": "N",
            }
        ],
    )
    db = database_path
    rebuild_database(data_dir, db)
    store = DuckDBStore(db)
    draft = build_rule_scan(store, _test_thresholds())
    rule_ids = {hit.rule_id for hit in draft.hits}
    # C1 退货占比 500/1000=50% ≥15% 且 ≥50；C3 负回款 150/200=75% 且 ≥50；
    # D1 负毛利亏损 1000*0.2=200 ≥50；D3 账期 200-60=140 ≥120
    assert "SLS_RETURN_ABNORMAL" in rule_ids
    assert "PAY_OFFSET_ABNORMAL" in rule_ids
    assert "CON_NEGATIVE_MARGIN" in rule_ids
    assert "CON_TERM_OVERAGE" in rule_ids


def test_v3_d3_amount_floor_and_customer_id(raw_data_dir: Path, database_path: Path) -> None:
    """D3 金额下限 + D1/D3 主键统一为 customer_id。"""

    from ict_agent.data import rebuild_database

    from tests.conftest import _write_csv

    data_dir = raw_data_dir
    # 客户 C040：大额账期超期（应立案）；客户 C041：小额账期超期（金额低于测试阈值 50，不立案）
    _write_csv(
        data_dir / "增值合同签约明细.csv",
        [
            {
                "申请日期": "2026-05-01",
                "合同编号": "Y1",
                "合同状态": "流程结束",
                "客户名称": "测试客户四十",
                "销售金额": 1000,
                "实估毛利率_不含税": 0.1,
                "实际净毛利率_不含税": 0.05,
                "合同文本账期": 60,
                "实际账期": 250,
                "开票金额1": 800,
            },
            {
                "申请日期": "2026-05-02",
                "合同编号": "Y2",
                "合同状态": "流程结束",
                "客户名称": "测试客户四一",
                "销售金额": 10,
                "实估毛利率_不含税": 0.1,
                "实际净毛利率_不含税": 0.05,
                "合同文本账期": 60,
                "实际账期": 250,
                "开票金额1": 8,
            },
        ],
    )
    _write_csv(
        data_dir / "客户授信.csv",
        [
            {
                "客户编号_中台": "C040",
                "客户名称": "测试客户四十",
                "授信额度": 1000,
                "黑白名单状态": 0,
                "黑白名单原因": "",
                "黑白名单创建时间": "2025-01-01",
                "失信分级": "一般",
                "净资产": 1000,
                "净利润": 100,
                "信用保险": "N",
            },
            {
                "客户编号_中台": "C041",
                "客户名称": "测试客户四一",
                "授信额度": 1000,
                "黑白名单状态": 0,
                "黑白名单原因": "",
                "黑白名单创建时间": "2025-01-01",
                "失信分级": "一般",
                "净资产": 1000,
                "净利润": 100,
                "信用保险": "N",
            },
        ],
    )
    db = database_path
    rebuild_database(data_dir, db)
    store = DuckDBStore(db)
    draft = build_rule_scan(store, _test_thresholds())
    case_map = {c.case_id: c for c in draft.cases}
    term_cases = {h.case_id for h in draft.hits if h.rule_id == "CON_TERM_OVERAGE"}
    # 只有大额 C040 立案，小额 C041 不立案
    assert any(case_map[cid].entity_id == "C040" for cid in term_cases)
    assert not any(case_map[cid].entity_id == "C041" for cid in term_cases)


def test_v2_a4_e2_no_duplicate_case(raw_data_dir: Path, database_path: Path) -> None:
    """A4/E2 合并：无授信+应收>=下限+有销售的客户只生成一个案件且 priority=HIGH。"""

    from ict_agent.data import rebuild_database

    from tests.conftest import _write_csv

    data_dir = raw_data_dir
    _write_csv(
        data_dir / "销售流水.csv",
        [
            {
                "出库日期": "2026-07-01",
                "客户编号": "C050",
                "客户名称": "测试客户五十",
                "合同号": "",
                "销售订单号": "S1",
                "库存组织名称": "W1",
                "物料编码": "M1",
                "数量": 10,
                "出库类型": "销售出库",
                "事务处理类型名称": "正常销售",
                "销售金额_折扣后_含税": 500,
                "出库成本金额": 300,
            }
        ],
    )
    _write_csv(
        data_dir / "应收快照_月末24期.csv",
        [
            {
                "快照时间": "2026-07-31",
                "合同号": "",
                "客户编号": "C050",
                "客户名称": "测试客户五十",
                "销售订单号": "S1",
                "应收金额": 300,
                "超期应收金额": 0,
                "超期30天以上金额": 0,
                "超期60天以上金额": 0,
                "最终承诺还款日期": "2026-08-31",
                "是否展期": "N",
                "超期天数": 0,
                "物料编码": "M1",
            }
        ],
    )
    _write_csv(
        data_dir / "客户授信.csv",
        [
            {
                "客户编号_中台": "C050",
                "客户名称": "测试客户五十",
                "授信额度": 0,
                "黑白名单状态": 0,
                "黑白名单原因": "",
                "黑白名单创建时间": "2025-01-01",
                "失信分级": "一般",
                "净资产": 1000,
                "净利润": 100,
                "信用保险": "N",
            }
        ],
    )
    db = database_path
    rebuild_database(data_dir, db)
    store = DuckDBStore(db)
    draft = build_rule_scan(store, _test_thresholds())
    c050_cases = [c for c in draft.cases if c.entity_id == "C050"]
    # 只生成一个案件
    assert len(c050_cases) == 1
    c = c050_cases[0]
    assert c.priority == "HIGH"
    # 该案件同时含 A4 和 E2 hit
    case_hits = [h.rule_id for h in draft.hits if h.case_id == c.case_id]
    assert "AR_NO_CREDIT_WITH_EXPOSURE" in case_hits
    assert "CREDIT_EXPOSURE_DECLINE" in case_hits


def test_v3_b4_large_overdue_hits_small_not(raw_data_dir: Path, database_path: Path) -> None:
    """B4 超期库存重做：大额短超期命中，小额长超期不命中。"""

    from ict_agent.data import rebuild_database

    from tests.conftest import _write_csv

    data_dir = raw_data_dir
    _write_csv(
        data_dir / "库龄快照_季末8期.csv",
        [
            {
                "快照日期": "2026-06-30",
                "物料编码": "BIG1",
                "库存组织名称": "W1",
                "数量": 10,
                "库龄": 100,
                "含税总价": 600000,
                "是否超期": "Y",
                "超期天数": 68,
            },
            {
                "快照日期": "2026-06-30",
                "物料编码": "SMALL1",
                "库存组织名称": "W1",
                "数量": 1,
                "库龄": 500,
                "含税总价": 1,
                "是否超期": "Y",
                "超期天数": 300,
            },
        ],
    )
    db = database_path
    rebuild_database(data_dir, db)
    store = DuckDBStore(db)
    draft = build_rule_scan(store, _test_thresholds())
    case_map = {c.case_id: c for c in draft.cases}
    overdue_cases = {h.case_id for h in draft.hits if h.rule_id == "INV_OVERDUE_STOCK"}
    # 大额短超期 BIG1（60万/68天 ≥ 阈值50万/60天）命中
    assert any(case_map[cid].entity_id == "BIG1|W1" for cid in overdue_cases)
    # 小额长超期 SMALL1（1000元/300天 < 金额阈值）不命中
    assert not any(case_map[cid].entity_id == "SMALL1|W1" for cid in overdue_cases)


def test_v3_d2_weighted_margin(raw_data_dir: Path, database_path: Path) -> None:
    """D2 金额加权聚合：一单多行不同毛利率不误判。"""

    from ict_agent.data import rebuild_database

    from tests.conftest import _write_csv

    data_dir = raw_data_dir
    # 合同 Z1 两行：行1 金额90/实估0.30/实际0.00（巨大高估）；行2 金额10/实估0.02/实际0.01
    # 加权后：实估=(90*0.30+10*0.02)/100=0.272；
    # 实际=(90*0+10*0.01)/100=0.001；差0.271>=5pt 且实际<2% → 命中
    _write_csv(
        data_dir / "增值合同签约明细.csv",
        [
            {
                "申请日期": "2026-05-01",
                "合同编号": "Z1",
                "合同状态": "流程结束",
                "客户名称": "测试客户六十",
                "销售金额": 90,
                "实估毛利率_不含税": 0.30,
                "实际净毛利率_不含税": 0.00,
                "合同文本账期": 60,
                "实际账期": 70,
                "开票金额1": 80,
            },
            {
                "申请日期": "2026-05-01",
                "合同编号": "Z1",
                "合同状态": "流程结束",
                "客户名称": "测试客户六十",
                "销售金额": 10,
                "实估毛利率_不含税": 0.02,
                "实际净毛利率_不含税": 0.01,
                "合同文本账期": 60,
                "实际账期": 70,
                "开票金额1": 9,
            },
        ],
    )
    db = database_path
    rebuild_database(data_dir, db)
    store = DuckDBStore(db)
    draft = build_rule_scan(store, _test_thresholds())
    rule_ids = {h.rule_id for h in draft.hits}
    assert "CON_MARGIN_OPTIMISTIC" in rule_ids


def test_case_store_preserves_idempotency_and_review(
    store: DuckDBStore,
    tmp_path: Path,
) -> None:
    draft = build_rule_scan(store, _test_thresholds())
    case_store = CaseStore(tmp_path / "cases.duckdb")

    first_created = case_store.save_rule_scan(draft.run, draft.cases, draft.hits)
    second_draft = build_rule_scan(store, _test_thresholds())
    second_created = case_store.save_rule_scan(
        second_draft.run, second_draft.cases, second_draft.hits
    )

    assert first_created == 4
    assert second_created == 0
    assert len(case_store.fetch_cases().rows) == 4

    case_id = str(case_store.fetch_cases().rows[0][0])
    assert case_store.transition_case(case_id, "PENDING_AGENT_REVIEW", "AGENT_REVIEWING")
    assert case_store.recover_interrupted_investigations() == 1
    assert case_store.fetch_case(case_id).rows[0][7] == "PENDING_AGENT_REVIEW"
    assert case_store.transition_case(case_id, "PENDING_AGENT_REVIEW", "PENDING_HUMAN_REVIEW")
    case_store.save_review(
        ReviewWrite(
            review_id="review-1",
            case_id=case_id,
            decision="CONFIRMED_RISK",
            reviewer="测试审核人",
            reason="证据支持风险成立。",
            created_at="2026-08-08T00:00:00+00:00",
        ),
        "ACTION_IN_PROGRESS",
    )

    assert case_store.fetch_case(case_id).rows[0][7] == "ACTION_IN_PROGRESS"
    assert case_store.fetch_reviews(case_id).rows[0][2] == "CONFIRMED_RISK"
