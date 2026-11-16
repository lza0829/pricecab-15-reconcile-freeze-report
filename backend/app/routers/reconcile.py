from fastapi import APIRouter
from pydantic import BaseModel

from app.services import reconcile_service as svc
from app.services import report_service as report_svc

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


class ReportCreateIn(BaseModel):
    note: str | None = None
    # Bounded demo knob so the pin-vs-apply conflict can be observed.
    hold_ms: int = 0


class PolicyIn(BaseModel):
    policy: str


@router.get("/sales")
def reconcile_sales():
    return svc.reconcile_sales()


@router.get("/reports")
def list_reports():
    return report_svc.list_frozen_reports()


@router.post("/reports")
def create_report(body: ReportCreateIn | None = None):
    return report_svc.create_frozen_report(
        note=body.note if body else None,
        hold_ms=body.hold_ms if body else 0,
    )


@router.get("/reports/{report_no}")
def get_report(report_no: str):
    return report_svc.get_frozen_report(report_no)


@router.get("/policy")
def get_policy():
    return report_svc.get_policy()


@router.put("/policy")
def set_policy(body: PolicyIn):
    return report_svc.set_policy(body.policy)
