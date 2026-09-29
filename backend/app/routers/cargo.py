"""货物装卸接口：维护货邮任务，覆盖填报单暂存、舱位查询、装机落库与三处清单读取。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, LoadingPayload, PageResult
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
    """按货邮编号与状态过滤货物装卸列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/draft", response_model=dict)
def get_draft() -> dict[str, Any]:
    """读取暂存的填报单；没有时返回空对象，不报错。"""
    return service.get_draft()


@router.post("/draft", response_model=ActionResult)
def save_draft(payload: EntryPayload) -> ActionResult:
    """暂存填报单：缺字段也能存，返回时逐项列出还缺哪一项。"""
    draft, missing = service.save_draft(payload.values)
    return ActionResult(ok=True, message="填报单已暂存，刷新页面不丢", entry=draft, missing=missing)


@router.get("/cabin-allocation", response_model=dict)
def cabin_allocation(flight: str | None = Query(default=None, description="对应航班号")) -> dict[str, Any]:
    """查询舱位分配；上游查不到时返回 503，前端留住已填内容并重试。"""
    cabin, error = service.query_cabin(flight)
    if error is not None:
        raise HTTPException(status_code=503, detail=error)
    return cabin


@router.post("/loading", response_model=ActionResult)
def submit_loading(payload: LoadingPayload) -> ActionResult:
    """装机提交：按货邮编号去重，同一批只留最新一版；不同批次先落库为准。失败不动已录内容。"""
    entry, message, missing = service.submit_loading(
        payload.values,
        batch_id=payload.batch_id,
        base_version=payload.base_version,
    )
    if entry is None:
        return ActionResult(ok=False, message=message, missing=missing)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/pending", response_model=PageResult[dict])
def pending_list(page: int = 1, size: int = 200) -> PageResult[dict]:
    """待处理清单：待装卸与装卸中的货邮，和填报单、装卸明细同一份台账。"""
    items, total = service.pending_entries()
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/details", response_model=PageResult[dict])
def loading_details(page: int = 1, size: int = 200) -> PageResult[dict]:
    """装卸明细：已装机与已入库的货邮，装机数与填报单、待处理清单对得上。"""
    items, total = service.loading_details()
    return PageResult(items=items, total=total, page=page, size=size)


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
