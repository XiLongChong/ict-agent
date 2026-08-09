"""Pydantic 数据契约测试。"""

import pytest
from ict_agent.models import ToolResult
from pydantic import ValidationError


def test_tool_result_requires_matching_row_width() -> None:
    with pytest.raises(ValidationError, match="columns"):
        ToolResult(
            summary="结果",
            columns=["a", "b"],
            rows=[[1]],
            sources=["sales"],
            period="2026-07-31",
        )
