"""通过与调查 Agent 相同的语义服务发现、搜索和查询只读业务证据。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from ict_agent.config import ConfigurationError, load_settings
from ict_agent.data import DataAccessError, DuckDBStore
from ict_agent.models import (
    BusinessRecordSearchQuery,
    EvidenceQuery,
    InvestigationProfile,
    JsonScalar,
)
from ict_agent.tools import (
    AnalysisInputError,
    discover_evidence_capabilities,
    query_business_evidence,
    search_business_records,
)
from pydantic import ValidationError


def _add_scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--investigation-profile",
        required=True,
        choices=["RECEIVABLES", "INVENTORY"],
    )
    parser.add_argument("--customer-id", help="应收案件客户编号，例如 C015。")
    parser.add_argument("--customer-name", default="", help="可选客户名称。")
    parser.add_argument("--material-code", help="库存案件物料编码。")
    parser.add_argument("--inventory-org", help="库存案件库存组织完整名称，例如 仓库W012。")


def parse_args() -> argparse.Namespace:
    """解析明确白名单参数；不存在执行 SQL 或读取文件的入口。"""

    settings = load_settings(require_api_key=False, require_data_dir=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=settings.database_path)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("snapshot", help="显示当前七表导入快照身份。")

    capabilities = commands.add_parser("capabilities", help="探测当前案件真实可用的证据能力。")
    _add_scope(capabilities)
    capabilities.add_argument("--observation-date", required=True, help="案件观察日。")

    search = commands.add_parser("search", help="在当前案件关联记录内搜索业务标识。")
    _add_scope(search)
    search.add_argument(
        "--record-type",
        required=True,
        choices=["customer", "contract", "order", "material"],
    )
    search.add_argument("--query", required=True, help="业务标识包含文本。")
    search.add_argument("--limit", type=int, default=10)

    query = commands.add_parser("query", help="执行一项注册的受控证据查询。")
    _add_scope(query)
    query.add_argument(
        "--dataset",
        required=True,
        choices=[
            "receivables",
            "sales_payments",
            "extensions",
            "credit",
            "contracts",
            "inventory",
            "sales",
        ],
    )
    query.add_argument(
        "--grain",
        required=True,
        choices=["customer", "month", "contract", "order", "quarter", "age_bucket"],
    )
    query.add_argument("--metric", action="append", required=True, help="指标，可重复传入。")
    query.add_argument(
        "--time-window",
        default="latest",
        choices=["latest", "last_3_months", "last_6_months", "last_12_months", "all"],
    )
    query.add_argument("--sort-by")
    query.add_argument("--sort-direction", default="desc", choices=["asc", "desc"])
    query.add_argument("--limit", type=int, default=30)
    return parser.parse_args()


def _scope(args: argparse.Namespace) -> tuple[InvestigationProfile, dict[str, JsonScalar]]:
    investigation_profile = cast(InvestigationProfile, args.investigation_profile)
    if investigation_profile == "RECEIVABLES":
        if not args.customer_id:
            raise AnalysisInputError("应收案件必须提供 --customer-id。")
        return investigation_profile, {
            "customer_id": args.customer_id,
            "customer_name": args.customer_name,
        }
    if not args.material_code or not args.inventory_org:
        raise AnalysisInputError("库存案件必须提供 --material-code 和 --inventory-org。")
    return investigation_profile, {
        "material_code": args.material_code,
        "inventory_org": args.inventory_org,
    }


def _snapshot_payload(store: DuckDBStore) -> dict[str, Any]:
    snapshot = store.get_snapshot()
    return {
        "snapshot_id": snapshot.snapshot_id,
        "imported_at": snapshot.imported_at,
        "schema_fingerprint": snapshot.schema_fingerprint,
        "sources": [item.__dict__ for item in snapshot.sources],
    }


def main() -> int:
    """执行只读命令并输出稳定 JSON。"""

    try:
        args = parse_args()
        store = DuckDBStore(args.database.resolve())
        if args.command == "snapshot":
            payload: Any = _snapshot_payload(store)
        else:
            investigation_profile, context = _scope(args)
            if args.command == "capabilities":
                payload = discover_evidence_capabilities(
                    store, investigation_profile, context, args.observation_date
                ).model_dump(mode="json")
            elif args.command == "search":
                payload = search_business_records(
                    store,
                    investigation_profile,
                    context,
                    BusinessRecordSearchQuery(
                        record_type=args.record_type,
                        query=args.query,
                        limit=args.limit,
                    ),
                ).model_dump(mode="json")
            else:
                payload = query_business_evidence(
                    store,
                    investigation_profile,
                    context,
                    EvidenceQuery(
                        dataset=args.dataset,
                        grain=args.grain,
                        metrics=args.metric,
                        time_window=args.time_window,
                        sort_by=args.sort_by,
                        sort_direction=args.sort_direction,
                        limit=args.limit,
                    ),
                ).model_dump(mode="json")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (ConfigurationError, DataAccessError, AnalysisInputError, ValidationError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
