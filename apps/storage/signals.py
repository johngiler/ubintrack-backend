from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from . import models


@receiver(post_save, sender=models.Position)
def update_total_levels_on_save(sender, instance, **kwargs):
    # After saving a Position, update total_levels of the related Cava
    cava = instance.cava
    cava.total_levels = models.Position.objects.filter(cava=cava).count()
    cava.save()


@receiver(post_delete, sender=models.Position)
def update_total_levels_on_delete(sender, instance, **kwargs):
    cava_id = instance.cava_id
    if not cava_id:
        return
    cava = models.Cava.objects.filter(pk=cava_id).first()
    if cava is None:
        return
    cava.total_levels = models.Position.objects.filter(cava_id=cava_id).count()
    cava.save(update_fields=["total_levels", "updated_at"])
