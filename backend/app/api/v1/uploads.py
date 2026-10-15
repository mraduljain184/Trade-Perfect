"""Bulk upload endpoints 15-17 (PROJECT_CONTEXT section 8.1). HTTP only: the work is in upload_service."""
from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_db, require_permission
from app.models import User
from app.schemas.base import ERR_AUTH, ErrorResponse
from app.schemas.upload import UploadAccepted, UploadBatchOut
from app.services import upload_service

router = APIRouter(tags=["Bulk upload"])

ERR_400 = {400: {"model": ErrorResponse, "description": "Not a .csv file, empty, or larger than 20 MB"}}
ERR_404 = {404: {"model": ErrorResponse, "description": "Batch not found (or uploaded by someone else)"}}


@router.post("/trades/bulk", response_model=UploadAccepted, status_code=202, responses={**ERR_AUTH, **ERR_400},
             summary="Upload a CSV of trades; it is processed in the background (poll GET /uploads/{batchId})")
def bulk_upload(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(..., description="CSV file"),
                db: Session = Depends(get_db),
                user: User = Depends(require_permission("TRADE_SUBMIT_BULK"))) -> UploadAccepted:
    file_name, path = upload_service.save_upload(file.filename, file.file)
    try:
        batch = upload_service.create_batch(db, file_name, user, client_ip(request))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    background_tasks.add_task(upload_service.process_batch, batch.id, path)
    return UploadAccepted(batch_id=batch.batch_id)


@router.get("/uploads/{batch_id}", response_model=UploadBatchOut, responses={**ERR_AUTH, **ERR_404},
            summary="Progress and summary of one upload")
def get_upload(batch_id: str, db: Session = Depends(get_db),
               user: User = Depends(require_permission("TRADE_SUBMIT_BULK"))) -> UploadBatchOut:
    return upload_service.to_out(db, upload_service.get_batch(db, batch_id, user))


@router.get("/uploads/{batch_id}/rejections", response_class=StreamingResponse, responses={**ERR_AUTH, **ERR_404},
            summary="Download the rejected rows of one upload as CSV, with the reason for each")
def get_rejections(batch_id: str, db: Session = Depends(get_db),
                   user: User = Depends(require_permission("TRADE_SUBMIT_BULK"))):
    chunks, file_name = upload_service.rejection_report(db, upload_service.get_batch(db, batch_id, user))
    return StreamingResponse(iter(chunks), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{file_name}"'})
