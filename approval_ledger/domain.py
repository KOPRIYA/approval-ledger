import hashlib
import json
from dataclasses import dataclass


class Forbidden(Exception):
    pass


class Conflict(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str
    scopes: frozenset

    def require(self, scope, tenant=None):
        if scope not in self.scopes or (tenant is not None and self.tenant != tenant):
            raise Forbidden("insufficient scope or tenant mismatch")


def validate_plan(plan, evidence):
    if not isinstance(plan, dict) or set(plan) != {"action", "service", "summary", "evidence_ids"}:
        raise ValueError("invalid proposal schema")
    if plan["action"] != "create_followup" or plan["service"] not in ("checkout", "catalog"):
        raise ValueError("tool or service is not allowlisted")
    if not isinstance(plan["summary"], str) or not 1 <= len(plan["summary"].strip()) <= 300:
        raise ValueError("summary must contain 1 to 300 characters")
    ids = plan["evidence_ids"]
    if not isinstance(ids, list) or not ids or any(type(i) is not str for i in ids):
        raise ValueError("evidence IDs required")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate evidence")
    allowed = {doc["id"] for doc in evidence if doc["service"] == plan["service"]}
    if not set(ids).issubset(allowed):
        raise ValueError("uncited or cross-service evidence")
    return plan
