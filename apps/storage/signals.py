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
    # After deleting a Position, update total_levels of the related Cava
    cava = instance.cava
    cava.total_levels = models.Position.objects.filter(cava=cava).count()
    cava.save()
