import random
import string
from django.apps import apps


def generate_random_id(length=15):
    return ''.join(random.choices(string.ascii_letters, k=length))


def generate_slug(name, limit):
    slug = name.replace(' ', '')
    return slug[0:limit].upper()


def get_installed_apps():
    return [
        app_config.label for app_config in apps.get_app_configs()
        if app_config.name.startswith('apps.')
    ]
