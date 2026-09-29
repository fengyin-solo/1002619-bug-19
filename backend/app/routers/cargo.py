"""货物装卸接口：货邮任务列表、可暂存的装机填报单、舱位分配查询与状态流转。

注意路由顺序：/summary、/pending、/allocation、/draft、/load、/export 这些静态路径
必须放在 /{entry_id} 之前，否则会被 entry_id 的 int 校验拦成 422。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, LoadResult, PageResult
from app.services.cargo import CargoService

router = APIRouter(prefix="/api/cargo", tags=["货物装卸"])

service = CargoService()

LIST_FIELDS = ["货邮编号", "对应航班", "货物类型", "总重吨位", "板箱数量", "装卸班组", "舱位分配", "装卸状态"]
STATUSES = ["待装卸", "装卸中", "已装机", "已入库"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按货邮编号检索"),
    status: str | None = Query(default=None, description="待装卸、装卸中、已装机、已入库"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按货邮编号与状态过滤货物装卸列表；缺字段的行照常返回，并标注缺哪一项。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    """装机数唯一口径：填报单、待处理清单、装卸明细三处都读这里，保证对得上。"""
    return service.summary()


@router.get("/pending", response_model=PageResult[dict])
def list_pending(page: int = 1, size: int = 200) -> PageResult[dict]:
    """待处理清单：还没入库的货邮任务，和装卸明细读同一份数据。"""
    items, total = service.list_pending(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/allocation")
def get_allocation(keyword: str = Query(default="", description="货邮编号或对应航班")) -> dict[str, Any]:
    """查舱位分配；查不到时返回 404 和可读说明，前端留住已填内容并给出重试。"""
    allocation = service.find_allocation(keyword)
    if allocation is None:
        raise HTTPException(status_code=404, detail=f"未查到「{keyword}」的舱位分配，可稍后重试")
    return allocation


@router.get("/draft")
def get_draft() -> dict[str, Any]:
    """读取暂存的填报单草稿；没有草稿时返回空行，页面照常可填。"""
    draft = service.get_draft()
    if draft is None:
        return {"rows": [], "saved_at": None}
    return draft


@router.put("/draft")
def save_draft(payload: EntryPayload) -> dict[str, Any]:
    """暂存填报单：允许缺字段，缺项只在页面上灰显提示，不拦暂存。"""
    raw_rows = payload.values.get("rows")
    if not isinstance(raw_rows, list):
        raise HTTPException(status_code=400, detail="填报单格式不对：rows 应为数组")
    rows = [row for row in raw_rows if isinstance(row, dict)]
    return service.save_draft(rows)


@router.delete("/draft")
def clear_draft() -> ActionResult:
    """清空暂存草稿。"""
    service.clear_draft()
    return ActionResult(ok=True, message="草稿已清空")


@router.post("/load", response_model=LoadResult)
def submit_load(payload: EntryPayload) -> LoadResult:
    """提交装机填报单：写库前按货邮编号去重，整批校验不过就一行都不写、原样退回。"""
    raw_rows = payload.values.get("rows")
    if not isinstance(raw_rows, list):
        return LoadResult(ok=False, message="填报单格式不对：rows 应为数组", form={"rows": []})
    rows = [row for row in raw_rows if isinstance(row, dict)]
    result = service.submit_load(rows)
    return LoadResult(**result)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出货物装卸清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "cargo", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条货邮任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"货邮任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条货邮任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="货邮任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条货邮任务执行安排装卸、开始装机、确认入库；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
