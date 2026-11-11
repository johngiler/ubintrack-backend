from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.models import BulkOperationJob
from apps.common.utils.bulk_jobs import serialize_bulk_job
from apps.workspaces.utils.tenant import get_workspace_for_request


class BulkOperationJobDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk: int):
        ws = get_workspace_for_request(request)
        if ws is None:
            return Response(
                {"detail": "No se pudo completar la operación."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job = BulkOperationJob.objects.filter(pk=pk, workspace=ws).first()
        if job is None:
            return Response(
                {"detail": "No encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(serialize_bulk_job(job))
