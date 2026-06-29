from django.contrib import admin

from apps.dashboard.models import EntriesCountersAgg, ExitsCountersAgg, BinsCountersAgg, DashboardDailyAgg, DashboardMonthlyAgg, HallStatsAgg, CavaStatsAgg


admin.site.register(EntriesCountersAgg)
admin.site.register(ExitsCountersAgg)
admin.site.register(BinsCountersAgg)
admin.site.register(DashboardDailyAgg)
admin.site.register(DashboardMonthlyAgg)
admin.site.register(CavaStatsAgg)
admin.site.register(HallStatsAgg)
