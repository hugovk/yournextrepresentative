# -*- coding: utf-8 -*-

from __future__ import unicode_literals

from os.path import join, realpath, dirname
from shutil import rmtree

from django.contrib.auth.models import User, Group
from django.contrib.sites.models import Site
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.urlresolvers import reverse
from django.test.utils import override_settings
from django.utils.six.moves.urllib_parse import urlsplit

from django_webtest import WebTest
from webtest import Upload

from ..models import QueuedImage, PHOTO_REVIEWERS_GROUP_NAME
from mysite.helpers import mkdir_p

from candidates.tests.factories import (
    PersonExtraFactory, CandidacyExtraFactory,
)
from candidates.tests.uk_examples import UK2015ExamplesMixin

TEST_MEDIA_ROOT = realpath(join(dirname(__file__), 'media'))


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class PhotoUploadTests(UK2015ExamplesMixin, WebTest):

    example_image_filename = join(
        settings.BASE_DIR, 'moderation_queue', 'tests', 'example-image.jpg'
    )

    @classmethod
    def setUpClass(cls):
        super(PhotoUploadTests, cls).setUpClass()
        storage = FileSystemStorage()
        desired_storage_path = join('queued-images', 'pilot.jpg')
        with open(cls.example_image_filename, 'rb') as f:
            cls.storage_filename = storage.save(desired_storage_path, f)
        mkdir_p(TEST_MEDIA_ROOT)

    @classmethod
    def tearDownClass(cls):
        rmtree(TEST_MEDIA_ROOT)
        super(PhotoUploadTests, cls).tearDownClass()

    def setUp(self):
        super(PhotoUploadTests, self).setUp()
        person_2009 = PersonExtraFactory.create(
            base__id='2009',
            base__name='Tessa Jowell'
        )
        person_2007 = PersonExtraFactory.create(
            base__id='2007',
            base__name='Tessa Jowell'
        )
        CandidacyExtraFactory.create(
            election=self.election,
            base__person=person_2009.base,
            base__post=self.dulwich_post_extra.base,
            base__on_behalf_of=self.labour_party_extra.base
        )
        CandidacyExtraFactory.create(
            election=self.election,
            base__person=person_2007.base,
            base__post=self.dulwich_post_extra.base,
            base__on_behalf_of=self.labour_party_extra.base
        )

        self.site = Site.objects.create(domain='example.com', name='YNR')
        self.test_upload_user = User.objects.create_user(
            'john',
            'john@example.com',
            'notagoodpassword',
        )
        self.test_upload_user.terms_agreement.assigned_to_dc = True
        self.test_upload_user.terms_agreement.save()
        self.test_reviewer = User.objects.create_superuser(
            'jane',
            'jane@example.com',
            'alsonotagoodpassword',
        )
        self.test_reviewer.terms_agreement.assigned_to_dc = True
        self.test_reviewer.terms_agreement.save()
        self.test_reviewer.groups.add(
            Group.objects.get(name=PHOTO_REVIEWERS_GROUP_NAME)
        )
        self.q1 = QueuedImage.objects.create(
            why_allowed='public-domain',
            justification_for_use="It's their Twitter avatar",
            decision='undecided',
            image=self.storage_filename,
            person=person_2009.base,
            user=self.test_upload_user
        )
        self.q2 = QueuedImage.objects.create(
            why_allowed='copyright-assigned',
            justification_for_use="I took this last week",
            decision='approved',
            image=self.storage_filename,
            person=person_2007.base,
            user=self.test_upload_user
        )
        self.q3 = QueuedImage.objects.create(
            why_allowed='other',
            justification_for_use="I found it somewhere",
            decision='rejected',
            image=self.storage_filename,
            person=person_2007.base,
            user=self.test_reviewer
        )
        self.q_no_uploading_user = QueuedImage.objects.create(
            why_allowed='profile-photo',
            justification_for_use='Auto imported from Twitter',
            decision='undecided',
            image=self.storage_filename,
            person=person_2009.base,
            user=None
        )

    def tearDown(self):
        self.q1.delete()
        self.q2.delete()
        self.q3.delete()
        self.test_upload_user.delete()
        self.test_reviewer.delete()
        self.site.delete()
        super(PhotoUploadTests, self).tearDown()

    def test_photo_upload_through_image_field(self):
        queued_images = QueuedImage.objects.all()
        initial_count = queued_images.count()
        upload_form_url = reverse(
            'photo-upload',
            kwargs={'person_id': '2009'}
        )
        form_page_response = self.app.get(
            upload_form_url,
            user=self.test_upload_user
        )
        form = form_page_response.forms['person-upload-photo-image']
        with open(self.example_image_filename, 'rb') as f:
            form['image'] = Upload('pilot.jpg', f.read())
        form['why_allowed'] = 'copyright-assigned'
        form['justification_for_use'] = 'I took this photo'
        upload_response = form.submit()
        self.assertEqual(upload_response.status_code, 302)

        split_location = urlsplit(upload_response.location)
        self.assertEqual('/moderation/photo/upload/2009/success', split_location.path)

        queued_images = QueuedImage.objects.all()
        self.assertEqual(initial_count + 1, queued_images.count())

        queued_image = queued_images.last()
        self.assertEqual(queued_image.decision, 'undecided')
        self.assertEqual(queued_image.why_allowed, 'copyright-assigned')
        self.assertEqual(
            queued_image.justification_for_use,
            'I took this photo'
        )
        self.assertEqual(queued_image.person.id, 2009)
        self.assertEqual(queued_image.user, self.test_upload_user)

    def test_photo_upload_through_url_field(self):
        upload_form_url = reverse(
            'photo-upload',
            kwargs={'person_id': '2009'}
        )
        form_page_response = self.app.get(
            upload_form_url,
            user=self.test_upload_user
        )
        form = form_page_response.forms['person-upload-photo-url']
        form['image_url'] = 'http://foo.com/bar.jpg'
        upload_response = form.submit()
        self.assertEqual(upload_response.status_code, 302)

    def test_shows_photo_policy_text_in_photo_upload_page(self):
        upload_form_url = reverse(
            'photo-upload',
            kwargs={'person_id': '2009'}
        )
        response = self.app.get(
            upload_form_url,
            user=self.test_upload_user
        )
        self.assertContains(response, 'Photo policy')
