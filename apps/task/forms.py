from django import forms

from . import models


class UserTaskTrackingForm(forms.ModelForm):
    class Meta:
        model = models.UserTaskTracking
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            user = self.instance.user
            self.fields["task"].queryset = models.Task.objects.filter(assigned_to=user)
        else:
            user = self.initial.get("user")
            if user:
                self.fields["task"].queryset = models.Task.objects.filter(
                    assigned_to=user)
            else:
                self.fields["task"].queryset = models.Task.objects.none()
