from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Customer, QueueEntry
from app.schemas import CustomerListOut

router = APIRouter(prefix="/queue/customers", tags=["customers"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[CustomerListOut])
def list_customers(for_date: date | None = None, db: Session = Depends(get_db)):
    query = db.query(Customer, func.count(QueueEntry.id).label("visit_count")).outerjoin(
        QueueEntry, QueueEntry.customer_id == Customer.id
    )

    if for_date is not None:
        customer_ids = (
            db.query(QueueEntry.customer_id).filter(QueueEntry.queue_date == for_date).subquery()
        )
        query = query.filter(Customer.id.in_(customer_ids))

    rows = query.group_by(Customer.id).order_by(Customer.name.asc()).all()
    return [
        CustomerListOut(
            id=customer.id,
            name=customer.name,
            phone=customer.phone,
            created_at=customer.created_at,
            visit_count=visit_count,
        )
        for customer, visit_count in rows
    ]
