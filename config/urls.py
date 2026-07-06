from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.utils.translation import gettext_lazy as _
from rest_framework import routers

from apps.core.api import views as core_views
from apps.dashboard.api import views as dashboard_views
from apps.finance.api import views as finance_views
from apps.inventory.api import views as inventory_views
from apps.order.api import views as order_views
from apps.storage.api import views as storage_views
from apps.task.api import views as task_views
from apps.workspaces.api.views import WorkspaceCurrentView, WorkspaceMeView

admin.site.site_header = _("Administración")
admin.site.site_title = _("Admin")
admin.site.index_title = _("Inicio")

router = routers.DefaultRouter()
router.register(r"users", core_views.CustomUserViewSet)
router.register(r"profiles", core_views.ProfileViewSet)
router.register(r"permissions", core_views.CustomPermissionViewSet)
router.register(r"clients", core_views.ClientViewSet)
router.register(r"branches", core_views.BranchViewSet)
router.register(r"products", inventory_views.ProductViewSet)
router.register(r"categories", inventory_views.CategoryViewSet)
router.register(r"bins", inventory_views.BinViewSet)
router.register(r"bin-products", inventory_views.BinProductViewSet)
router.register(r"storages", storage_views.StorageViewSet)
router.register(r"cavas", storage_views.CavaViewSet)
router.register(r"positions", storage_views.PositionViewSet)
router.register(r"entries", order_views.EntryViewSet)
router.register(r"entry-products", order_views.EntryProductViewSet)
router.register(r"entry-details", order_views.EntryDetailViewSet)
router.register(r"exits", order_views.ExitViewSet)
router.register(r"exit-products", order_views.ExitProductViewSet)
router.register(r"exit-product-groups", order_views.ExitProductGroupViewSet)
router.register(r"exit-details", order_views.ExitDetailViewSet)
router.register(r"tariffs", finance_views.TariffViewSet)
router.register(r"billing", finance_views.BillingCalculationViewSet, basename="billing")
router.register(r"tasks", task_views.TaskViewSet)
router.register(r"task-logs", task_views.TaskLogViewSet)
router.register(r"task-trackings", task_views.UserTaskTrackingViewSet)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/login/", core_views.CustomObtainAuthToken.as_view()),
    path("api/me/", core_views.MeProfileView.as_view()),
    path("api/workspace/current/", WorkspaceCurrentView.as_view(), name="workspace-current"),
    path("api/me/workspace/", WorkspaceMeView.as_view(), name="workspace-me"),
    path("api/dashboard/stats/", dashboard_views.DashboardStatsView.as_view(), name="dashboard-stats"),
    path("api/warehouse/structure/", dashboard_views.WarehouseStructureView.as_view(), name="warehouse-structure"),
    path("api/dashboard/client-stats/", dashboard_views.ClientStatsView.as_view(), name="client-stats"),
    path("api/nav/counts/", dashboard_views.NavCountsView.as_view(), name="nav-counts"),
    path("api/", include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
