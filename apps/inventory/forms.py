from django import forms

from apps.storage.models import Position
from apps.order.models import EntryProduct, ExitProduct
from apps.core.user_roles import get_user_client

from . import models


class ProductForm(forms.ModelForm):
    class Meta:
        model = models.Product
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.request:
            try:
                client = get_user_client(self.request.user)
                self.fields['client'].initial = client
            except:
                pass


class BinForm(forms.ModelForm):
    class Meta:
        model = models.Bin
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk and self.instance.entry:
            cavas = self.instance.entry.storage.cavas.all()
            self.fields['position'].queryset = Position.objects.filter(
                cava__in=cavas,
                is_active=True
            )
        else:
            self.fields['position'].queryset = Position.objects.none()


class BinProductForm(forms.ModelForm):
    class Meta:
        model = models.BinProduct
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.bin_instance:
            self.fields['entry_product'].queryset = EntryProduct.objects.filter(
                entry=self.bin_instance.entry)
        else:
            self.fields['entry_product'].queryset = EntryProduct.objects.none()
