# -*- coding: utf-8 -*-
# Generated by Django 1.9.13 on 2018-04-24 19:21
from __future__ import unicode_literals

from django.db import migrations
from django.db.models import Count


def add_pee_values(apps, schema_editor):
    ResultSet = apps.get_model("uk_results", "ResultSet")
    for rs in ResultSet.objects.all():
        rs.post_election = rs.post_election_result.post_election
        rs.save()


def remove_duplicate_resultsets(apps, schema_editor):
    ResultSet = apps.get_model("uk_results", "ResultSet")
    duplicates = ResultSet.objects.values(
        'post_election'
    ).order_by(
        'post_election'
    ).annotate(
        Count('post_election')
    ).filter(
        post_election__count__gt=1
    ).values_list(
        'post_election', flat=True
    )
    for pee_id in duplicates:
        resultsets = ResultSet.objects.filter(
            post_election_id=pee_id).order_by('-created')
        keep = resultsets[0]
        for rs in resultsets[1:]:
            assert rs.created < keep.created
            rs.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('uk_results', '0039_resultset_post_election'),
    ]

    operations = [
        migrations.RunPython(
            add_pee_values, migrations.RunPython.noop,
        ),
        migrations.RunPython(
            remove_duplicate_resultsets, migrations.RunPython.noop,
        ),
    ]
