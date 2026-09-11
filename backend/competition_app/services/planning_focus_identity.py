"""Request-local identity choices, without classifying or normalizing prose."""
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from competition_app.contracts.planning_request import (
    NumberedFocusAssessment, PlanningFocusAssessment, PlanningRequestScope,
)


class FocusIdentityError(ValueError):
    def __init__(self, field: str, code: str, actual: Any, allowed: Any):
        self.issue = {"field": field, "code": code, "actual": actual, "allowed": allowed}
        super().__init__(f"{field}: {code}")


class PlanningFocusIdentityCatalog:
    def __init__(self, scope: PlanningRequestScope, route_context: dict, evidence_ids: list[str]):
        self.objects = {i: name for i, name in enumerate(scope.objects, 1)}
        route = (route_context.get("textbook_route") or {}).get("route") or {}
        self.stages = {}
        self.books = {}
        for stage_no, stage in enumerate(route.get("stages") or [], 1):
            self.stages[stage_no] = deepcopy(stage)
            for name in stage.get("books") or []:
                self.books[len(self.books) + 1] = (stage_no, name)
        self.evidence_ids = list(dict.fromkeys(evidence_ids))

    def model_catalog(self) -> dict:
        return {
            "objects": [{"object_no": i, "name": name} for i, name in self.objects.items()],
            "stages": [{"stage_no": i, "stage_id": stage.get("stage_id"), "name": stage.get("name")}
                       for i, stage in self.stages.items()],
            "books": [{"book_no": i, "stage_no": stage_no, "name": name}
                      for i, (stage_no, name) in self.books.items()],
            "evidence_ids": self.evidence_ids,
        }

    @staticmethod
    def _array_choices(node: dict, values: list) -> None:
        if values:
            node["items"]["enum"] = values
        else:
            node["maxItems"] = 0

    def schema(self) -> dict:
        schema = NumberedFocusAssessment.model_json_schema()
        props = schema["properties"]
        self._array_choices(props["focus_object_nos"], list(self.objects))
        props["focus_object_nos"].update(minItems=len(self.objects), maxItems=len(self.objects), uniqueItems=True)
        self._array_choices(props["focus_book_nos"], list(self.books))
        props["focus_book_nos"]["uniqueItems"] = True
        props["focus_stage_no"] = {"anyOf": [
            *([{"type": "integer", "enum": list(self.stages)}] if self.stages else []),
            {"type": "null"},
        ]}
        link = schema["$defs"]["NumberedFocusEvidence"]["properties"]
        if self.objects:
            link["object_no"]["enum"] = list(self.objects)
        if self.evidence_ids:
            link["evidence_id"]["enum"] = self.evidence_ids
        else:
            props["evidence_links"]["maxItems"] = 0
        return schema

    def bind(self, raw: Any) -> PlanningFocusAssessment:
        value = NumberedFocusAssessment.model_validate(raw)
        nos = value.focus_object_nos
        if len(nos) != len(self.objects) or set(nos) != set(self.objects):
            raise FocusIdentityError("focus_object_nos", "exact_object_coverage_required", nos, list(self.objects))
        stage_no = value.focus_stage_no
        if stage_no is not None and stage_no not in self.stages:
            raise FocusIdentityError("focus_stage_no", "unknown_stage_no", stage_no, list(self.stages))
        if value.status == "sufficient" and stage_no is None:
            raise FocusIdentityError("focus_stage_no", "stage_required", None, list(self.stages))
        books = value.focus_book_nos
        allowed_books = [i for i, (owner, _) in self.books.items() if owner == stage_no]
        if len(set(books)) != len(books):
            raise FocusIdentityError("focus_book_nos", "duplicate_book_no", books, allowed_books)
        if (value.status == "sufficient" and not books) or any(i not in allowed_books for i in books):
            raise FocusIdentityError("focus_book_nos", "books_must_belong_to_selected_stage", books, allowed_books)
        links = []
        for index, link in enumerate(value.evidence_links):
            if link.object_no not in self.objects:
                raise FocusIdentityError(f"evidence_links/{index}/object_no", "unknown_object_no", link.object_no, list(self.objects))
            if link.evidence_id not in self.evidence_ids:
                raise FocusIdentityError(f"evidence_links/{index}/evidence_id", "unknown_evidence_id", link.evidence_id, self.evidence_ids)
            links.append({"name": self.objects[link.object_no], "evidence_id": link.evidence_id})
        return PlanningFocusAssessment(
            status=value.status, focus_names=[self.objects[i] for i in nos],
            focus_stage_id=self.stages[stage_no].get("stage_id") if stage_no is not None else None,
            focus_books=[self.books[i][1] for i in books], evidence_links=links,
            cross_stage_mode=value.cross_stage_mode, source_quote=value.source_quote, reason=value.reason,
        )

    def feedback(self, exc: ValueError, raw: Any) -> dict:
        if isinstance(exc, FocusIdentityError):
            issues = [exc.issue]
        elif isinstance(exc, ValidationError):
            issues = [{"field": "/".join(map(str, e["loc"])), "code": e["type"], "message": e["msg"]}
                      for e in exc.errors(include_url=False, include_input=False)[:24]]
        else:
            # Exact validator message is feedback only, never a routing keyword.
            issues = [{"field": "assessment", "code": "assessment_validation_failed", "message": str(exc)[:1000]}]
        return {"issues": issues, "previous_output": deepcopy(raw), "allowed_catalog": self.model_catalog()}