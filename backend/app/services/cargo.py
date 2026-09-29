"""货物装卸业务规则：状态流转、字段校验与筛选口径都收在这里。

装机填报单的关键约束：
- 写库前先按货邮编号去重，同一批重复出现的只留最新一版；
- 板箱数量只允许持平或增加，不允许比已录的少；
- 两人同时装机时以先落库的为准，后到整批退回；
- 任何一行校验不过，整批不写库，原填报内容退回给前端。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "cargo"
REQUIRED_FIELDS = ["货邮编号", "对应航班", "货物类型"]
LOAD_REQUIRED_FIELDS = ["货邮编号", "对应航班", "货物类型", "板箱数量"]
ENTRY_FIELDS = ["货邮编号", "对应航班", "货物类型", "总重吨位", "板箱数量", "装卸班组", "舱位分配"]
STATUS_ORDER = ["待装卸", "装卸中", "已装机", "已入库"]
ACTION_RULES = {"安排装卸": "装卸中", "开始装机": "已装机", "确认入库": "已入库"}
NEGATIVE_ACTIONS: list[str] = []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_uld(value: Any) -> int | None:
    """把板箱数量解析成非负整数；空值或解析不了都按未填处理。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        count = int(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            count = int(text)
        except ValueError:
            try:
                count = int(float(text))
            except ValueError:
                return None
    return count if count >= 0 else None


def _missing_fields(row: dict[str, Any]) -> list[str]:
    """缺哪些关键字段：列表灰显和填报单提示都用这一份口径。"""
    missing = [field for field in REQUIRED_FIELDS if not _text(row.get(field))]
    if _parse_uld(row.get("板箱数量")) is None:
        missing.append("板箱数量")
    return missing


def _annotate(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    missing = _missing_fields(item)
    item["missing_fields"] = missing
    item["missing_text"] = f"缺：{'、'.join(missing)}" if missing else ""
    return item


class CargoService:
    def __init__(self) -> None:
        self._draft: dict[str, Any] | None = None
        self._draft_lock = threading.Lock()
        self._load_lock = threading.Lock()

    # ---------- 列表 / 明细 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in _text(row.get("货邮编号"))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [_annotate(row) for row in rows[start:start + size]], total

    def list_pending(self, *, page: int = 1, size: int = 200) -> tuple[list[dict[str, Any]], int]:
        """待处理清单：还没走到已入库的任务，和明细读同一份数据。"""
        rows = [row for row in store.rows(MODULE) if row.get("pending")]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [_annotate(row) for row in rows[start:start + size]], total

    def summary(self) -> dict[str, Any]:
        """装机数唯一口径：填报单、待处理清单、装卸明细都读这里。"""
        rows = store.rows(MODULE)
        counts = {status: 0 for status in STATUS_ORDER}
        for row in rows:
            status = _text(row.get("status")) or STATUS_ORDER[0]
            counts[status] = counts.get(status, 0) + 1
        return {
            "total": len(rows),
            "pending": sum(1 for row in rows if row.get("pending")),
            "loaded": counts.get("已装机", 0),
            "counts": counts,
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return _annotate(row) if row is not None else None

    # ---------- 舱位分配查询 ----------

    def find_allocation(self, keyword: str | None) -> dict[str, Any] | None:
        """按货邮编号或航班查舱位分配；查不到返回 None，由路由转成可读的 404。"""
        key = _text(keyword)
        if not key:
            return None
        for row in store.rows(MODULE):
            if key not in (_text(row.get("货邮编号")), _text(row.get("对应航班"))):
                continue
            allocation = _text(row.get("舱位分配"))
            if allocation:
                return {
                    "货邮编号": row.get("货邮编号"),
                    "对应航班": row.get("对应航班"),
                    "舱位分配": allocation,
                }
        return None

    # ---------- 填报单草稿 ----------

    def get_draft(self) -> dict[str, Any] | None:
        with self._draft_lock:
            if self._draft is None:
                return None
            return {"rows": [dict(row) for row in self._draft["rows"]], "saved_at": self._draft["saved_at"]}

    def save_draft(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """暂存填报单：允许缺字段，缺项由前端灰显提示，不拦暂存。"""
        cleaned = [{field: row.get(field) for field in ENTRY_FIELDS} for row in rows]
        with self._draft_lock:
            self._draft = {"rows": cleaned, "saved_at": datetime.now().isoformat(timespec="seconds")}
            return {"rows": [dict(row) for row in cleaned], "saved_at": self._draft["saved_at"]}

    def clear_draft(self) -> None:
        with self._draft_lock:
            self._draft = None

    # ---------- 登记与状态流转 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        # 全部业务字段都要留下来，不能只存必填项，否则已录的板箱数量会丢。
        entry.update({field: values.get(field) for field in ENTRY_FIELDS})
        uld = _parse_uld(values.get("板箱数量"))
        entry["板箱数量"] = uld
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return _annotate(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"货邮任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于货物装卸可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return _annotate(entry), f"货邮任务已{action}"

    # ---------- 装机填报单提交 ----------

    def submit_load(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """提交装机填报单。

        返回 {"ok", "message", "loaded", "failures", "form"}：
        - 整批校验不过时一行都不写库，form 里退回原填报内容；
        - 同批按货邮编号去重，重复出现的只留最新一版；
        - 已装机的编号以先落库为准，后到的整批退回。
        """
        original = [{field: row.get(field) for field in ENTRY_FIELDS} for row in rows]
        form = {"rows": original}
        if not rows:
            return {"ok": False, "message": "填报单为空，没有可装机的行", "loaded": [], "failures": [], "form": form}

        with self._load_lock:
            # 1) 写库前去重：同一批里同一货邮编号只留最新一版（后出现的覆盖先出现的）。
            deduped: dict[str, tuple[int, dict[str, Any]]] = {}
            failures: list[dict[str, Any]] = []
            for index, row in enumerate(original):
                code = _text(row.get("货邮编号"))
                if not code:
                    failures.append({"row": index + 1, "货邮编号": "", "reason": "缺：货邮编号"})
                    continue
                normalized = dict(row)
                normalized["货邮编号"] = code
                normalized["板箱数量"] = _parse_uld(row.get("板箱数量"))
                deduped[code] = (index, normalized)

            # 2) 逐行校验：缺字段、板箱数量不合法、与已录数据冲突。
            existing_rows = store.rows(MODULE)
            by_code = {
                _text(row.get("货邮编号")): row for row in existing_rows if _text(row.get("货邮编号"))
            }
            candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
            for code, (index, row) in deduped.items():
                missing = [field for field in REQUIRED_FIELDS if not _text(row.get(field))]
                if row["板箱数量"] is None:
                    missing.append("板箱数量")
                if missing:
                    failures.append({"row": index + 1, "货邮编号": code, "reason": f"缺：{'、'.join(missing)}"})
                    continue
                if row["板箱数量"] < 1:
                    failures.append({"row": index + 1, "货邮编号": code, "reason": "板箱数量需为不小于 1 的整数"})
                    continue
                existing = by_code.get(code)
                if existing is not None:
                    if existing.get("status") == "已装机":
                        failures.append({
                            "row": index + 1,
                            "货邮编号": code,
                            "reason": "该货邮编号已装机落库，以先落库的记录为准",
                        })
                        continue
                    recorded = _parse_uld(existing.get("板箱数量"))
                    if recorded is not None and row["板箱数量"] < recorded:
                        failures.append({
                            "row": index + 1,
                            "货邮编号": code,
                            "reason": f"板箱数量不能少于已录的 {recorded} 箱",
                        })
                        continue
                candidates.append((row, existing))

            # 3) 任何一行不过，整批退回，不写库。
            if failures:
                return {
                    "ok": False,
                    "message": f"装机未落库：{len(failures)} 行未通过校验，原填报内容已退回",
                    "loaded": [],
                    "failures": failures,
                    "form": form,
                }

            # 4) 全部通过才落库；已有编号做更新，新编号按序补 id。
            next_id = max((int(row.get("id", 0)) for row in existing_rows), default=0) + 1
            loaded: list[dict[str, Any]] = []
            for row, existing in candidates:
                target = existing
                if target is None:
                    target = {"id": next_id}
                    next_id += 1
                    existing_rows.append(target)
                for field in ENTRY_FIELDS:
                    target[field] = row.get(field)
                target["status"] = "已装机"
                target["pending"] = True
                target["abnormal"] = False
                loaded.append(_annotate(target))

        # 5) 落库成功后清掉草稿，避免旧草稿被再次提交。
        self.clear_draft()
        return {
            "ok": True,
            "message": f"装机完成，已落库 {len(loaded)} 票",
            "loaded": loaded,
            "failures": [],
            "form": None,
        }
