import json

from django.contrib.gis.geos import Polygon
from django.urls import reverse
from django.test import TestCase, override_settings

from social_django.models import UserSocialAuth
from rest_framework.test import APITestCase

from ...users.models import User
from ...feature.tests.modelfactories import FeatureFactory
from ..models import SuspicionReasons, Tag, Changeset
from ..views import ChangesetListAPIView, PaginatedCSVRenderer
from .modelfactories import (
    ChangesetFactory, SuspectChangesetFactory, GoodChangesetFactory,
    HarmfulChangesetFactory, TagFactory, UserWhitelistFactory
    )


class TestChangesetListView(APITestCase):

    def setUp(self):
        SuspectChangesetFactory.create_batch(26)
        ChangesetFactory.create_batch(26)
        # list endpoints will not list Changesets with user=""
        ChangesetFactory(user="")
        self.user = User.objects.create_user(
            username='test',
            password='password',
            email='a@a.com',
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            )
        self.url = reverse('changeset:list')

    def test_unauthenticated_changeset_list_response(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['features']), 50)
        self.assertEqual(response.data['count'], 52)
        self.assertNotIn(
            'user',
            response.data['features'][0]['properties'].keys()
            )
        self.assertNotIn(
            'uid',
            response.data['features'][0]['properties'].keys()
            )
        self.assertNotIn(
            'check_user',
            response.data['features'][0]['properties'].keys()
            )

    def test_authenticated_changeset_list_response(self):
        self.client.login(username=self.user.username, password='password')
        response = self.client.get(self.url)
        self.assertIn('user', response.data['features'][0]['properties'].keys())
        self.assertIn('uid', response.data['features'][0]['properties'].keys())
        self.assertIn(
            'check_user',
            response.data['features'][0]['properties'].keys()
            )

    def test_pagination(self):
        response = self.client.get(self.url, {'page': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['features']), 2)
        self.assertEqual(response.data['count'], 52)
        # test page_size parameter
        response = self.client.get(self.url, {'page_size': 60})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['features']), 52)

    def test_filters(self):
        response = self.client.get(self.url, {'in_bbox': '-72,43,-70,45'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 52)

        response = self.client.get(self.url, {'in_bbox': '-3.17,-91.98,-2.1,-90.5'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

        response = self.client.get(self.url, {'is_suspect': 'true'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 26)

        response = self.client.get(self.url, {'is_suspect': 'false'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 26)

        response = self.client.get(self.url, {'users': 'another_user'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 52)

        response = self.client.get(self.url, {'checked_by': 'another_user'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 52)

        response = self.client.get(self.url, {'uids': '98978,43323'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 52)

    def test_authenticated_user_filters(self):
        """Test if the users, check_users and uids filters works to
        authenticated users.
        """
        self.client.login(username=self.user.username, password='password')

        response = self.client.get(self.url, {'users': 'another_user'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

        response = self.client.get(self.url, {'users': 'test'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 52)

        response = self.client.get(self.url, {'checked_by': 'another_user'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

        response = self.client.get(self.url, {'uids': '98978,43323'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

        response = self.client.get(self.url, {'uids': '123123'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 52)

    def test_area_lt_filter(self):
        """Test in_bbox in combination with area_lt filter field."""
        ChangesetFactory(
            bbox=Polygon([(0, 0), (0, 3), (3, 3), (3, 0), (0, 0)])
            )
        response = self.client.get(self.url, {'in_bbox': '0,0,1,1', 'area_lt': 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

        response = self.client.get(self.url, {'in_bbox': '0,0,1,1', 'area_lt': 8})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

        response = self.client.get(self.url, {'in_bbox': '0,0,2,2', 'area_lt': 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

        response = self.client.get(self.url, {'in_bbox': '0,0,2,2', 'area_lt': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

    def test_hide_whitelist_filter(self):
        UserWhitelistFactory(user=self.user, whitelist_user='test')

        # test without login
        response = self.client.get(self.url, {'hide_whitelist': 'true'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 52)
        response = self.client.get(
            self.url,
            {'hide_whitelist': 'true', 'checked': 'true'}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

        # test with login. As all changesets in the DB are from a whitelisted
        # user, the features count will be zero
        self.client.login(username=self.user.username, password='password')
        response = self.client.get(self.url, {'hide_whitelist': 'true'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

    def test_csv_renderer(self):
        self.assertIn(
            PaginatedCSVRenderer,
            ChangesetListAPIView().renderer_classes
            )
        response = self.client.get(self.url, {'format': 'csv', 'page_size': 60})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['features']), 52)
        response = self.client.get(
            self.url,
            {'is_suspect': 'true', 'format': 'csv'}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['features']), 26)


class TestChangesetFilteredViews(APITestCase):
    def setUp(self):
        ChangesetFactory()
        SuspectChangesetFactory()
        HarmfulChangesetFactory()
        GoodChangesetFactory()

    def test_suspect_changesets_view(self):
        url = reverse('changeset:suspect-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 3)

    def test_no_suspect_changesets_view(self):
        url = reverse('changeset:no-suspect-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

    def test_harmful_changesets_view(self):
        url = reverse('changeset:harmful-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

    def test_no_harmful_changesets_view(self):
        url = reverse('changeset:no-harmful-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

    def test_checked_changesets_view(self):
        self.user = User.objects.create_user(
            username='test',
            password='password',
            email='a@a.com',
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            )
        self.client.login(username=self.user.username, password='password')
        url = reverse('changeset:checked-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertTrue(
            response.data['features'][0]['properties']['check_user'].startswith(
                'user '
                )
            )

    def test_unchecked_changesets_view(self):
        url = reverse('changeset:unchecked-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)


class TestChangesetListViewOrdering(APITestCase):

    def setUp(self):
        SuspectChangesetFactory.create_batch(2, delete=2)
        HarmfulChangesetFactory.create_batch(24, form_create=20, modify=2, delete=40)
        GoodChangesetFactory.create_batch(24, form_create=1000, modify=20)
        self.url = reverse('changeset:list')

    def test_ordering(self):
        # default ordering is by descending id
        response = self.client.get(self.url)
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.all()]
            )
        # ascending id
        response = self.client.get(self.url, {'order_by': 'id'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('id')]
            )
        # ascending date ordering
        response = self.client.get(self.url, {'order_by': 'date'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('date')]
            )
        # descending date ordering
        response = self.client.get(self.url, {'order_by': '-date'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('-date')]
            )
        # ascending check_date
        response = self.client.get(self.url, {'order_by': 'check_date'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('check_date')]
            )
        # descending check_date ordering
        response = self.client.get(self.url, {'order_by': '-check_date'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('-check_date')]
            )
        # ascending create ordering
        response = self.client.get(self.url, {'order_by': 'create'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('create')]
            )
        # descending create ordering
        response = self.client.get(self.url, {'order_by': '-create'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('-create')]
            )
        # ascending modify ordering
        response = self.client.get(self.url, {'order_by': 'modify'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('modify')]
            )
        # descending modify ordering
        response = self.client.get(self.url, {'order_by': '-modify'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('-modify')]
            )
        # ascending delete ordering
        response = self.client.get(self.url, {'order_by': 'delete'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('delete')]
            )
        # descending delete ordering
        response = self.client.get(self.url, {'order_by': '-delete'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.order_by('-delete')]
            )

    def test_invalid_ordering_field(self):
        # default ordering is by descending id
        response = self.client.get(self.url, {'order_by': 'user'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [i.id for i in Changeset.objects.all()]
            )

    def test_number_reasons_ordering(self):
        changeset_1, changeset_2 = Changeset.objects.all()[:2]
        self.reason_1 = SuspicionReasons.objects.create(name='possible import')
        self.reason_1.changesets.add(changeset_1)
        self.reason_2 = SuspicionReasons.objects.create(name='suspect word')
        self.reason_2.changesets.add(changeset_1, changeset_2)

        response = self.client.get(
            self.url,
            {'order_by': '-number_reasons', 'page_size': 2}
            )
        self.assertEqual(
            [i['id'] for i in response.data.get('features')],
            [changeset_1.id, changeset_2.id]
            )

        response = self.client.get(self.url, {'order_by': 'number_reasons'})
        self.assertEqual(
            [i['id'] for i in response.data.get('features')[-2:]],
            [changeset_2.id, changeset_1.id]
            )


class TestChangesetDetailView(APITestCase):

    def setUp(self):
        self.reason_1 = SuspicionReasons.objects.create(name='possible import')
        self.reason_2 = SuspicionReasons.objects.create(name='suspect word')
        self.reason_3 = SuspicionReasons.objects.create(
            name='Big edit in my city',
            is_visible=False
            )
        self.changeset = HarmfulChangesetFactory(id=31982803)
        self.feature = FeatureFactory(changeset=self.changeset)
        self.invisible_feature = FeatureFactory(changeset=self.changeset)
        self.reason_1.changesets.add(self.changeset)
        self.reason_2.changesets.add(self.changeset)
        self.reason_2.features.add(self.feature)
        self.reason_3.features.add(self.feature, self.invisible_feature)
        self.reason_3.changesets.add(self.changeset)
        self.tag = Tag.objects.create(name='Vandalism')
        self.tag.changesets.add(self.changeset)

    def test_unauthenticated_changeset_detail_response(self):
        response = self.client.get(
            reverse('changeset:detail', args=[self.changeset.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get('id'), 31982803)
        self.assertIn('geometry', response.data.keys())
        self.assertIn('properties', response.data.keys())
        self.assertNotIn('uid', response.data['properties'].keys())
        self.assertEqual(
            self.changeset.editor,
            response.data['properties']['editor']
            )
        self.assertNotIn('user', response.data['properties'])
        self.assertEqual(
            self.changeset.imagery_used,
            response.data['properties']['imagery_used']
            )
        self.assertEqual(
            self.changeset.source,
            response.data['properties']['source']
            )
        self.assertEqual(
            self.changeset.comment,
            response.data['properties']['comment']
            )
        self.assertEqual(
            self.changeset.create,
            response.data['properties']['create']
            )
        self.assertEqual(
            self.changeset.modify,
            response.data['properties']['modify']
            )
        self.assertEqual(
            self.changeset.delete,
            response.data['properties']['delete']
            )
        self.assertNotIn('check_user', response.data['properties'])
        self.assertTrue(response.data['properties']['is_suspect'])
        self.assertTrue(response.data['properties']['checked'])
        self.assertTrue(response.data['properties']['harmful'])
        self.assertIn('date', response.data['properties'].keys())
        self.assertIn('check_date', response.data['properties'].keys())
        self.assertEqual(len(response.data['properties']['features']), 1)
        self.assertEqual(
            self.feature.osm_id,
            response.data['properties']['features'][0]['osm_id']
            )
        self.assertEqual(
            self.feature.url,
            response.data['properties']['features'][0]['url']
            )
        self.assertEqual(
            response.data['properties']['features'][0]['name'],
            'Test'
            )
        self.assertEqual(
            len(response.data['properties']['features'][0]['reasons']),
            1
            )
        self.assertIn(
            {'id': self.reason_2.id, 'name': 'suspect word'},
            response.data['properties']['features'][0]['reasons']
            )

    def test_authenticated_changeset_detail_response(self):
        self.user = User.objects.create_user(
            username='test',
            password='password',
            email='a@a.com',
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            )
        self.client.login(username=self.user.username, password='password')
        response = self.client.get(
            reverse('changeset:detail', args=[self.changeset.id])
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.changeset.uid, response.data['properties']['uid'])
        self.assertEqual(self.changeset.user, response.data['properties']['user'])
        self.assertEqual(
            self.changeset.check_user.name,
            response.data['properties']['check_user']
            )

    def test_changeset_detail_response_with_staff_user(self):
        self.user = User.objects.create_user(
            username='test',
            password='password',
            email='a@a.com',
            is_staff=True
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            )
        self.client.login(username=self.user.username, password='password')
        response = self.client.get(
            reverse('changeset:detail', args=[self.changeset.id])
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(response.data['properties']['features']),
            2
            )
        self.assertIn(
            {'id': self.reason_2.id, 'name': 'suspect word'},
            response.data['properties']['features'][0]['reasons']
            )
        self.assertIn(
            {'id': self.reason_3.id, 'name': 'Big edit in my city'},
            response.data['properties']['features'][0]['reasons']
            )

    def test_feature_without_name_tag(self):
        self.feature.geojson = json.dumps({'properties': {'osm:type': 'node'}})
        self.feature.save()
        response = self.client.get(
            reverse('changeset:detail', args=[self.changeset.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(
            response.data['properties']['features'][0]['name']
            )


class TestReasonsAndTagFieldsInChangesetViews(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='test',
            password='password',
            email='a@a.com',
            is_staff=True
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            )
        self.reason_1 = SuspicionReasons.objects.create(name='possible import')
        self.reason_2 = SuspicionReasons.objects.create(name='suspect word')
        self.reason_3 = SuspicionReasons.objects.create(
            name='Big edit in my city',
            is_visible=False
            )
        self.changeset = HarmfulChangesetFactory(id=31982803)
        self.reason_1.changesets.add(self.changeset)
        self.reason_2.changesets.add(self.changeset)
        self.reason_3.changesets.add(self.changeset)
        self.tag_1 = Tag.objects.create(name='Vandalism')
        self.tag_2 = Tag.objects.create(
            name='Vandalism in my city',
            is_visible=False
            )
        self.tag_1.changesets.add(self.changeset)
        self.tag_2.changesets.add(self.changeset)

    def test_detail_view_by_normal_user(self):
        response = self.client.get(reverse('changeset:detail', args=[self.changeset.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['properties']['reasons']), 2)
        self.assertEqual(len(response.data['properties']['tags']), 1)
        self.assertIn(
            {'id': self.reason_1.id, 'name': 'possible import'},
            response.data['properties']['reasons']
            )
        self.assertIn(
            {'id': self.reason_2.id, 'name': 'suspect word'},
            response.data['properties']['reasons']
            )
        self.assertIn(
            {'id': self.tag_1.id, 'name': 'Vandalism'},
            response.data['properties']['tags']
            )

    def test_detail_view_by_admin(self):
        self.client.login(username=self.user.username, password='password')
        response = self.client.get(reverse('changeset:detail', args=[self.changeset.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            {'id': self.reason_3.id, 'name': 'Big edit in my city'},
            response.data['properties']['reasons']
            )
        self.assertEqual(len(response.data['properties']['reasons']), 3)
        self.assertEqual(len(response.data['properties']['tags']), 2)
        self.assertIn(
            {'id': self.tag_2.id, 'name': 'Vandalism in my city'},
            response.data['properties']['tags']
            )
        self.assertEqual(response.data.get('id'), 31982803)
        self.assertIn('geometry', response.data.keys())
        self.assertIn('properties', response.data.keys())
        self.assertEqual(self.changeset.uid, response.data['properties']['uid'])
        self.assertEqual(
            self.changeset.editor,
            response.data['properties']['editor']
            )
        self.assertEqual(self.changeset.user, response.data['properties']['user'])
        self.assertEqual(
            self.changeset.imagery_used,
            response.data['properties']['imagery_used']
            )
        self.assertEqual(
            self.changeset.source,
            response.data['properties']['source']
            )
        self.assertEqual(
            self.changeset.comment,
            response.data['properties']['comment']
            )
        self.assertEqual(
            self.changeset.create,
            response.data['properties']['create']
            )
        self.assertEqual(
            self.changeset.modify,
            response.data['properties']['modify']
            )
        self.assertEqual(
            self.changeset.delete,
            response.data['properties']['delete']
            )
        self.assertEqual(
            self.changeset.check_user.name,
            response.data['properties']['check_user']
            )
        self.assertTrue(response.data['properties']['is_suspect'])
        self.assertTrue(response.data['properties']['checked'])
        self.assertTrue(response.data['properties']['harmful'])
        self.assertIn('date', response.data['properties'].keys())
        self.assertIn('check_date', response.data['properties'].keys())
        self.assertEqual(len(response.data['properties']['features']), 0)

    def test_list_view_by_normal_user(self):
        response = self.client.get(reverse('changeset:list'))
        self.assertEqual(response.status_code, 200)
        reasons = response.data['features'][0]['properties']['reasons']
        tags = response.data['features'][0]['properties']['tags']
        self.assertEqual(len(reasons), 2)
        self.assertEqual(len(tags), 1)
        self.assertIn(
            {'id': self.reason_1.id, 'name': 'possible import'},
            reasons
            )
        self.assertIn({'id': self.reason_2.id, 'name': 'suspect word'}, reasons)
        self.assertIn({'id': self.tag_1.id, 'name': 'Vandalism'}, tags)

    def test_list_view_by_admin(self):
        self.client.login(username=self.user.username, password='password')
        response = self.client.get(reverse('changeset:list'))
        self.assertEqual(response.status_code, 200)
        reasons = response.data['features'][0]['properties']['reasons']
        tags = response.data['features'][0]['properties']['tags']
        self.assertEqual(len(reasons), 3)
        self.assertEqual(len(tags), 2)
        self.assertIn(
            {'id': self.reason_3.id, 'name': 'Big edit in my city'},
            reasons
            )
        self.assertIn(
            {'id': self.tag_2.id, 'name': 'Vandalism in my city'},
            tags
            )


class TestCheckChangesetViews(APITestCase):

    def setUp(self):
        self.reason_1 = SuspicionReasons.objects.create(name='possible import')
        self.reason_2 = SuspicionReasons.objects.create(name='suspect_word')
        self.changeset = SuspectChangesetFactory(
            id=31982803, user='test', uid='123123'
            )
        self.changeset_2 = SuspectChangesetFactory(
            id=31982804, user='test2', uid='999999', editor='iD',
            )
        self.reason_1.changesets.add(self.changeset)
        self.reason_2.changesets.add(self.changeset)
        self.user = User.objects.create_user(
            username='test',
            password='password',
            email='a@a.com'
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            extra_data={
                'id': '123123',
                'access_token': {
                    'oauth_token': 'aaaa',
                    'oauth_token_secret': 'bbbb'
                    }
                }
            )
        self.tag_1 = TagFactory(name='Illegal import')
        self.tag_2 = TagFactory(name='Vandalism')

    def test_set_harmful_changeset_unlogged(self):
        """Anonymous users can't mark a changeset as harmful."""
        response = self.client.put(
            reverse('changeset:set-harmful', args=[self.changeset])
            )
        self.assertEqual(response.status_code, 401)
        self.changeset.refresh_from_db()
        self.assertIsNone(self.changeset.harmful)
        self.assertFalse(self.changeset.checked)
        self.assertIsNone(self.changeset.check_user)
        self.assertIsNone(self.changeset.check_date)

    def test_set_good_changeset_unlogged(self):
        """Anonymous users can't mark a changeset as good."""
        response = self.client.put(
            reverse('changeset:set-good', args=[self.changeset])
            )
        self.assertEqual(response.status_code, 401)
        self.changeset.refresh_from_db()
        self.assertIsNone(self.changeset.harmful)
        self.assertFalse(self.changeset.checked)
        self.assertIsNone(self.changeset.check_user)
        self.assertIsNone(self.changeset.check_date)

    def test_set_harmful_changeset_not_allowed(self):
        """User can't mark his own changeset as harmful."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:set-harmful', args=[self.changeset])
            )
        self.assertEqual(response.status_code, 403)
        self.changeset.refresh_from_db()
        self.assertIsNone(self.changeset.harmful)
        self.assertFalse(self.changeset.checked)
        self.assertIsNone(self.changeset.check_user)
        self.assertIsNone(self.changeset.check_date)

    def test_set_good_changeset_not_allowed(self):
        """User can't mark his own changeset as good."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:set-good', args=[self.changeset])
            )
        self.assertEqual(response.status_code, 403)
        self.changeset.refresh_from_db()
        self.assertIsNone(self.changeset.harmful)
        self.assertFalse(self.changeset.checked)
        self.assertIsNone(self.changeset.check_user)
        self.assertIsNone(self.changeset.check_date)

    def test_set_harmful_changeset_get(self):
        """GET is not an allowed method in the set_harmful URL."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.get(
            reverse('changeset:set-harmful', args=[self.changeset_2]),
            )

        self.assertEqual(response.status_code, 405)
        self.changeset_2.refresh_from_db()
        self.assertIsNone(self.changeset_2.harmful)
        self.assertFalse(self.changeset_2.checked)
        self.assertIsNone(self.changeset_2.check_user)
        self.assertIsNone(self.changeset_2.check_date)

    def test_set_harmful_changeset_put(self):
        """User can set a changeset of another user as harmful with a PUT request.
        We can also set the tags of the changeset sending it as data.
        """
        self.client.login(username=self.user.username, password='password')
        data = {'tags': [self.tag_1.id, self.tag_2.id]}
        response = self.client.put(
            reverse('changeset:set-harmful', args=[self.changeset_2.pk]),
            data
            )

        self.assertEqual(response.status_code, 200)
        self.changeset_2.refresh_from_db()
        self.assertTrue(self.changeset_2.harmful)
        self.assertTrue(self.changeset_2.checked)
        self.assertEqual(self.changeset_2.check_user, self.user)
        self.assertIsNotNone(self.changeset_2.check_date)
        self.assertEqual(self.changeset_2.tags.count(), 2)
        self.assertIn(
            self.tag_1,
            self.changeset_2.tags.all()
            )
        self.assertIn(
            self.tag_2,
            self.changeset_2.tags.all()
            )

    def test_set_harmful_changeset_with_invalid_tag_id(self):
        """Return a 400 error if a user try to add a invalid tag id to a changeset.
        """
        self.client.login(username=self.user.username, password='password')
        data = {'tags': [self.tag_1.id, 87765, 898986]}
        response = self.client.put(
            reverse('changeset:set-harmful', args=[self.changeset_2.pk]),
            data
            )

        self.assertEqual(response.status_code, 400)
        self.changeset_2.refresh_from_db()
        self.assertIsNone(self.changeset_2.harmful)
        self.assertFalse(self.changeset_2.checked)
        self.assertIsNone(self.changeset_2.check_user)
        self.assertIsNone(self.changeset_2.check_date)
        self.assertEqual(self.changeset_2.tags.count(), 0)

    def test_set_harmful_changeset_put_without_data(self):
        """Test marking a changeset as harmful without sending data (so the
        changeset will not receive tags).
        """
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:set-harmful', args=[self.changeset_2.pk])
            )

        self.assertEqual(response.status_code, 200)
        self.changeset_2.refresh_from_db()
        self.assertTrue(self.changeset_2.harmful)
        self.assertTrue(self.changeset_2.checked)
        self.assertEqual(self.changeset_2.check_user, self.user)
        self.assertIsNotNone(self.changeset_2.check_date)
        self.assertEqual(self.changeset_2.tags.count(), 0)

    def test_set_good_changeset_get(self):
        """GET is not an allowed method in the set_good URL."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.get(
            reverse('changeset:set-good', args=[self.changeset_2]),
            )

        self.assertEqual(response.status_code, 405)
        self.changeset_2.refresh_from_db()
        self.assertIsNone(self.changeset_2.harmful)
        self.assertFalse(self.changeset_2.checked)
        self.assertIsNone(self.changeset_2.check_user)
        self.assertIsNone(self.changeset_2.check_date)

    def test_set_good_changeset_put(self):
        """User can set a changeset of another user as good with a PUT request.
        We can also set the tags of the changeset sending it as data.
        """
        self.client.login(username=self.user.username, password='password')
        data = {'tags': [self.tag_1.id, self.tag_2.id]}
        response = self.client.put(
            reverse('changeset:set-good', args=[self.changeset_2]),
            data
            )
        self.assertEqual(response.status_code, 200)
        self.changeset_2.refresh_from_db()
        self.assertFalse(self.changeset_2.harmful)
        self.assertTrue(self.changeset_2.checked)
        self.assertEqual(self.changeset_2.check_user, self.user)
        self.assertIsNotNone(self.changeset_2.check_date)
        self.assertEqual(self.changeset_2.tags.count(), 2)
        self.assertIn(
            self.tag_1,
            self.changeset_2.tags.all()
            )
        self.assertIn(
            self.tag_2,
            self.changeset_2.tags.all()
            )

    def test_set_good_changeset_with_invalid_tag_id(self):
        """Return a 400 error if a user try to add a invalid tag id to a changeset.
        """
        self.client.login(username=self.user.username, password='password')
        data = {'tags': [self.tag_1.id, 87765, 898986]}
        response = self.client.put(
            reverse('changeset:set-good', args=[self.changeset_2.pk]),
            data
            )

        self.assertEqual(response.status_code, 400)
        self.changeset_2.refresh_from_db()
        self.assertIsNone(self.changeset_2.harmful)
        self.assertFalse(self.changeset_2.checked)
        self.assertIsNone(self.changeset_2.check_user)
        self.assertIsNone(self.changeset_2.check_date)
        self.assertEqual(self.changeset_2.tags.count(), 0)

    def test_set_good_changeset_put_without_data(self):
        """Test marking a changeset as good without sending data (so the
        changeset will not receive tags).
        """
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:set-good', args=[self.changeset_2]),
            )
        self.assertEqual(response.status_code, 200)
        self.changeset_2.refresh_from_db()
        self.assertFalse(self.changeset_2.harmful)
        self.assertTrue(self.changeset_2.checked)
        self.assertEqual(self.changeset_2.check_user, self.user)
        self.assertIsNotNone(self.changeset_2.check_date)

    def test_404(self):
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:set-good', args=[4988787832]),
            )
        self.assertEqual(response.status_code, 404)

        response = self.client.put(
            reverse('changeset:set-harmful', args=[4988787832]),
            )
        self.assertEqual(response.status_code, 404)

    def test_try_to_check_changeset_already_checked(self):
        """A PUT request to set_harmful or set_good urls of a checked changeset
        will not change anything on it.
        """
        changeset = HarmfulChangesetFactory(uid=333)
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:set-good', args=[changeset.pk]),
            )
        self.assertEqual(response.status_code, 403)
        changeset.refresh_from_db()
        self.assertNotEqual(changeset.check_user, self.user)

        data = {'tags': [self.tag_1.id, self.tag_2.id]}
        response = self.client.put(
            reverse('changeset:set-harmful', args=[changeset.pk]),
            data,
            )
        self.assertEqual(response.status_code, 403)
        changeset.refresh_from_db()
        self.assertNotEqual(changeset.check_user, self.user)


class TestUncheckChangesetView(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='test_2',
            password='password',
            email='a@a.com'
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            extra_data={
                'id': '123123',
                'access_token': {
                    'oauth_token': 'aaaa',
                    'oauth_token_secret': 'bbbb'
                    }
                }
            )
        self.suspect_changeset = SuspectChangesetFactory()
        self.good_changeset = GoodChangesetFactory(check_user=self.user)
        self.harmful_changeset = HarmfulChangesetFactory(check_user=self.user)
        self.harmful_changeset_2 = HarmfulChangesetFactory()
        self.tag = TagFactory(name='Vandalism')
        self.tag.changesets.set([
            self.good_changeset,
            self.harmful_changeset,
            self.harmful_changeset_2
            ])

    def test_unauthenticated_response(self):
        response = self.client.put(
            reverse('changeset:uncheck', args=[self.harmful_changeset.pk]),
            )
        self.assertEqual(response.status_code, 401)
        self.harmful_changeset.refresh_from_db()
        self.assertTrue(self.harmful_changeset.harmful)
        self.assertTrue(self.harmful_changeset.checked)
        self.assertEqual(self.harmful_changeset.check_user, self.user)
        self.assertIsNotNone(self.harmful_changeset.check_date)
        self.assertEqual(self.harmful_changeset.tags.count(), 1)
        self.assertIn(self.tag, self.harmful_changeset.tags.all())

    def test_uncheck_harmful_changeset(self):
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:uncheck', args=[self.harmful_changeset.pk]),
            )
        self.assertEqual(response.status_code, 200)
        self.harmful_changeset.refresh_from_db()
        self.assertIsNone(self.harmful_changeset.harmful)
        self.assertFalse(self.harmful_changeset.checked)
        self.assertIsNone(self.harmful_changeset.check_user)
        self.assertIsNone(self.harmful_changeset.check_date)
        self.assertEqual(self.harmful_changeset.tags.count(), 1)

    def test_uncheck_good_changeset(self):
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:uncheck', args=[self.good_changeset.pk]),
            )
        self.assertEqual(response.status_code, 200)
        self.good_changeset.refresh_from_db()
        self.assertIsNone(self.good_changeset.harmful)
        self.assertFalse(self.good_changeset.checked)
        self.assertIsNone(self.good_changeset.check_user)
        self.assertIsNone(self.good_changeset.check_date)
        self.assertEqual(self.good_changeset.tags.count(), 1)

    def test_common_user_uncheck_permission(self):
        """Common user can only uncheck changesets that he checked."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:uncheck', args=[self.harmful_changeset_2.pk]),
            )

        self.assertEqual(response.status_code, 403)
        self.harmful_changeset.refresh_from_db()
        self.assertTrue(self.harmful_changeset_2.harmful)
        self.assertTrue(self.harmful_changeset_2.checked)
        self.assertIsNotNone(self.harmful_changeset_2.check_user)
        self.assertIsNotNone(self.harmful_changeset_2.check_date)

    def test_try_to_uncheck_unchecked_changeset(self):
        """It's not possible to uncheck an unchecked changeset!"""
        self.client.login(username=self.user.username, password='password')
        response = self.client.put(
            reverse('changeset:uncheck', args=[self.suspect_changeset.pk]),
            )
        self.assertEqual(response.status_code, 403)

    def test_staff_user_uncheck_any_changeset(self):
        """A staff user can uncheck changesets checked by any user."""
        staff_user = User.objects.create_user(
            username='staff_test',
            password='password',
            email='s@a.com',
            is_staff=True
            )
        UserSocialAuth.objects.create(
            user=staff_user,
            provider='openstreetmap',
            uid='87873',
            )
        self.client.login(username=staff_user.username, password='password')
        response = self.client.put(
            reverse('changeset:uncheck', args=[self.good_changeset.pk]),
            )
        self.assertEqual(response.status_code, 200)
        response = self.client.put(
            reverse('changeset:uncheck', args=[self.harmful_changeset.pk]),
            )
        self.assertEqual(response.status_code, 200)
        response = self.client.put(
            reverse('changeset:uncheck', args=[self.harmful_changeset_2.pk]),
            )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(Changeset.objects.filter(checked=True).count(), 0)


class TestAddTagToChangeset(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='user',
            email='c@a.com',
            password='password',
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='999',
            )
        self.changeset_user = User.objects.create_user(
            username='test',
            email='b@a.com',
            password='password',
            )
        UserSocialAuth.objects.create(
            user=self.changeset_user,
            provider='openstreetmap',
            uid='123123',
            )
        self.changeset = ChangesetFactory()
        self.checked_changeset = HarmfulChangesetFactory(check_user=self.user)
        self.tag = TagFactory(name='Not verified')

    def test_unauthenticated_can_not_add_tag(self):
        response = self.client.post(
            reverse('changeset:tags', args=[self.changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.changeset.tags.count(), 0)

    def test_can_not_add_invalid_tag_id(self):
        """When the tag id does not exist, it will return a 404 response."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.post(
            reverse('changeset:tags', args=[self.changeset.id, 44534])
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.changeset.tags.count(), 0)

    def test_add_tag(self):
        """A user that is not the creator of the changeset can add tags to an
        unchecked changeset.
        """
        self.client.login(username=self.user.username, password='password')
        response = self.client.post(
            reverse('changeset:tags', args=[self.changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.changeset.tags.count(), 1)
        self.assertIn(self.tag, self.changeset.tags.all())

        # test add the same tag again
        response = self.client.post(
            reverse('changeset:tags', args=[self.changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.changeset.tags.count(), 1)

    def test_add_tag_by_changeset_owner(self):
        """The user that created the changeset can not add tags to it."""
        self.client.login(username=self.changeset_user.username, password='password')
        response = self.client.post(
            reverse('changeset:tags', args=[self.changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.changeset.tags.count(), 0)

    def test_add_tag_to_checked_changeset(self):
        """The user that checked the changeset can add tags to it."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.post(
            reverse('changeset:tags', args=[self.checked_changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.checked_changeset.tags.count(), 1)
        self.assertIn(self.tag, self.checked_changeset.tags.all())

    def test_other_user_can_not_add_tag_to_checked_changeset(self):
        """A non staff user can not add tags to a changeset that other user have
        checked.
        """
        other_user = User.objects.create_user(
            username='other_user',
            email='b@a.com',
            password='password',
            )
        UserSocialAuth.objects.create(
            user=other_user,
            provider='openstreetmap',
            uid='28763',
            )
        self.client.login(username=other_user.username, password='password')
        response = self.client.post(
            reverse('changeset:tags', args=[self.checked_changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.changeset.tags.count(), 0)

    def test_staff_user_add_tag_to_checked_changeset(self):
        """A staff user can add tags to a changeset."""
        staff_user = User.objects.create_user(
            username='admin',
            email='b@a.com',
            password='password',
            is_staff=True
            )
        UserSocialAuth.objects.create(
            user=staff_user,
            provider='openstreetmap',
            uid='28763',
            )
        self.client.login(username=staff_user.username, password='password')
        response = self.client.post(
            reverse('changeset:tags', args=[self.checked_changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.checked_changeset.tags.count(), 1)
        self.assertIn(self.tag, self.checked_changeset.tags.all())


class TestRemoveTagToChangeset(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='user',
            email='c@a.com',
            password='password',
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='999',
            )
        self.changeset_user = User.objects.create_user(
            username='test',
            email='b@a.com',
            password='password',
            )
        UserSocialAuth.objects.create(
            user=self.changeset_user,
            provider='openstreetmap',
            uid='123123',
            )
        self.changeset = ChangesetFactory()
        self.checked_changeset = HarmfulChangesetFactory(check_user=self.user)
        self.tag = TagFactory(name='Not verified')
        self.changeset.tags.add(self.tag)
        self.checked_changeset.tags.add(self.tag)

    def test_unauthenticated_can_not_remove_tag(self):
        response = self.client.delete(
            reverse('changeset:tags', args=[self.changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.changeset.tags.count(), 1)

    def test_can_not_remove_invalid_tag_id(self):
        """When the tag id does not exist it will return a 404 response."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.delete(
            reverse('changeset:tags', args=[self.changeset.id, 44534])
            )
        self.assertEqual(response.status_code, 404)

    def test_remove_tag(self):
        """A user that is not the creator of the changeset can remote tags to an
        unchecked changeset.
        """
        self.client.login(username=self.user.username, password='password')
        response = self.client.delete(
            reverse('changeset:tags', args=[self.changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.changeset.tags.count(), 0)

    def test_remove_tag_by_changeset_owner(self):
        """The user that created the changeset can not remove its tags."""
        self.client.login(username=self.changeset_user.username, password='password')
        response = self.client.delete(
            reverse('changeset:tags', args=[self.changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.changeset.tags.count(), 1)

    def test_remove_tag_of_checked_changeset(self):
        """The user that checked the changeset can remove its tags."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.delete(
            reverse('changeset:tags', args=[self.checked_changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.checked_changeset.tags.count(), 0)

    def test_other_user_can_not_remove_tag_to_checked_changeset(self):
        """A non staff user can not remove tags of a changeset that other user
        have checked.
        """
        other_user = User.objects.create_user(
            username='other_user',
            email='b@a.com',
            password='password',
            )
        UserSocialAuth.objects.create(
            user=other_user,
            provider='openstreetmap',
            uid='28763',
            )
        self.client.login(username=other_user.username, password='password')
        response = self.client.delete(
            reverse('changeset:tags', args=[self.checked_changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.checked_changeset.tags.count(), 1)

    def test_staff_user_remove_tag_to_checked_changeset(self):
        """A staff user can remove tags to a changeset."""
        staff_user = User.objects.create_user(
            username='admin',
            email='b@a.com',
            password='password',
            is_staff=True
            )
        UserSocialAuth.objects.create(
            user=staff_user,
            provider='openstreetmap',
            uid='28763',
            )
        self.client.login(username=staff_user.username, password='password')
        response = self.client.delete(
            reverse('changeset:tags', args=[self.checked_changeset.id, self.tag.id])
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.checked_changeset.tags.count(), 0)


class TestThrottling(APITestCase):
    def setUp(self):
        self.changesets = SuspectChangesetFactory.create_batch(
            5, user='test2', uid='999999', editor='iD',
            )
        self.user = User.objects.create_user(
            username='test',
            password='password',
            email='a@a.com'
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            )

    def test_set_harmful_throttling(self):
        """User can only check 3 changesets each minute."""
        self.client.login(username=self.user.username, password='password')
        for changeset in self.changesets:
            response = self.client.put(
                reverse('changeset:set-harmful', args=[changeset.pk]),
                )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Changeset.objects.filter(checked=True).count(), 3)

    def test_set_good_throttling(self):
        self.client.login(username=self.user.username, password='password')
        for changeset in self.changesets:
            response = self.client.put(
                reverse('changeset:set-good', args=[changeset.pk]),
                )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Changeset.objects.filter(checked=True).count(), 3)

    def test_mixed_throttling(self):
        """Test if both set_harmful and set_good views are throttled together."""
        self.client.login(username=self.user.username, password='password')
        three_changesets = self.changesets[:3]
        for changeset in three_changesets:
            response = self.client.put(
                reverse('changeset:set-good', args=[changeset.pk]),
                )
        self.assertEqual(response.status_code, 200)

        response = self.client.put(
            reverse('changeset:set-harmful', args=[self.changesets[3].pk]),
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Changeset.objects.filter(checked=True).count(), 3)

    def test_set_good_by_staff_user(self):
        """Staff users have not limit of checked changesets by minute."""
        user = User.objects.create_user(
            username='test_staff',
            password='password',
            email='a@a.com',
            is_staff=True
            )
        UserSocialAuth.objects.create(
            user=user,
            provider='openstreetmap',
            uid='8987',
            )
        self.client.login(username=user.username, password='password')
        for changeset in self.changesets:
            response = self.client.put(
                reverse('changeset:set-good', args=[changeset.pk]),
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Changeset.objects.filter(checked=True).count(), 5)

    def test_set_harmful_by_staff_user(self):
        """Staff users have not limit of checked changesets by minute."""
        user = User.objects.create_user(
            username='test_staff',
            password='password',
            email='a@a.com',
            is_staff=True
            )
        UserSocialAuth.objects.create(
            user=user,
            provider='openstreetmap',
            uid='8987',
            )
        self.client.login(username=user.username, password='password')
        for changeset in self.changesets:
            response = self.client.put(
                reverse('changeset:set-harmful', args=[changeset.pk]),
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Changeset.objects.filter(checked=True).count(), 5)


class TestAddFeatureToChangesetView(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='test',
            password='password',
            email='a@a.com'
            )
        UserSocialAuth.objects.create(
            user=self.user,
            provider='openstreetmap',
            uid='123123',
            )

        self.staff_user = User.objects.create_user(
            username='staff_test',
            password='password',
            email='a@a.com',
            is_staff=True
            )
        UserSocialAuth.objects.create(
            user=self.staff_user,
            provider='openstreetmap',
            uid='443324',
            )

        self.data = {
            "osm_id": 877656232,
            "osm_type": "node",
            "version": 54,
            "changeset": 1234,
            "name": "Salvador",
            "reasons": ["Deleted place", "Deleted wikidata"]
            }
        self.data_2 = {
            "osm_id": 877656333,
            "osm_type": "node",
            "version": 44,
            "changeset": 1234,
            "reasons": ["Deleted address"],
            "note": "suspect to be a graffiti",
            "uid": 9999,
            "user": "TestUser"
            }
        self.data_3 = {
            "osm_id": 87765444,
            "changeset": 4965,
            "osm_type": "node",
            "version": 44,
            "reasons": ["Deleted Motorway"]
            }
        self.changeset = ChangesetFactory(id=4965)
        self.url = reverse('changeset:add-feature')

    def test_unauthenticated_can_not_add_feature(self):
        """Unauthenticated requests should return a 401 error."""
        response = self.client.post(self.url, data=self.data)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Changeset.objects.filter(id=1234).count(), 0)

    def test_non_staff_user_can_not_add_feature(self):
        """Non staff users requests should return a 403 error."""
        self.client.login(username=self.user.username, password='password')
        response = self.client.post(self.url, data=self.data)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Changeset.objects.filter(id=1234).count(), 0)

    def test_add_feature(self):
        """When adding a feature to a changeset that does not exist in the
        database, it must create the changeset with the basic info contained in
        the feature.
        """
        self.client.login(username=self.staff_user.username, password='password')
        response = self.client.post(self.url, data=self.data)
        self.assertEqual(response.status_code, 200)
        reasons = SuspicionReasons.objects.filter(
            name__in=self.data.get('reasons')
            )
        self.assertEqual(
            Changeset.objects.get(id=self.data.get('changeset')).new_features,
            [{
                "osm_id": 877656232,
                "url": "node-877656232",
                "version": 54,
                "name": "Salvador",
                "reasons": [i.id for i in reasons]
                }]
        )
        self.assertEqual(
            Changeset.objects.get(id=self.data.get('changeset')).reasons.count(),
            2
            )

        # Add another feature to the same changeset
        response = self.client.post(self.url, data=self.data_2)
        reasons_2 = SuspicionReasons.objects.filter(
            name__in=self.data_2.get('reasons')
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(Changeset.objects.get(id=1234).new_features),
            2
            )
        self.assertIn(
            877656232,
            [i.get('osm_id') for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertIn(
            877656333,
            [i.get('osm_id') for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertIn(
            "node-877656232",
            [i.get('url') for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertIn(
            "node-877656333",
            [i.get('url') for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertIn(
            [i.id for i in reasons],
            [i.get('reasons') for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertIn(
            set([i.id for i in reasons_2]),
            [set(i.get('reasons')) for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertIn(
            "suspect to be a graffiti",
            [i.get('note') for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertIn(
            54,
            [i.get('version') for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertIn(
            44,
            [i.get('version') for i in Changeset.objects.get(id=1234).new_features],
            )
        self.assertEqual(Changeset.objects.get(id=1234).reasons.count(), 3)

    def test_add_feature_with_reason_id(self):
        """When creating a changeset, we can inform the id of the reason instead
        of the name.
        """
        self.client.login(username=self.staff_user.username, password='password')
        reason = SuspicionReasons.objects.create(name='Deleted address')
        payload = {
            "osm_id": 877656232,
            "changeset": 1234,
            "osm_type": "node",
            "version": 54,
            "name": "Tall Building",
            "reasons": [reason.id],
            "note": "suspect to be a graffiti"
            }
        response = self.client.post(self.url, data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Changeset.objects.get(id=self.data.get('changeset')).new_features,
            [{
                "osm_id": 877656232,
                "url": "node-877656232",
                "version": 54,
                "name": "Tall Building",
                "reasons": [reason.id],
                "note": "suspect to be a graffiti"
            }]
        )
        self.assertEqual(
            Changeset.objects.get(id=self.data.get('changeset')).reasons.count(),
            1
            )

    def test_add_feature_to_existent_changeset(self):
        """Adding a feature to an existent changeset."""
        self.client.login(username=self.staff_user.username, password='password')
        response = self.client.post(self.url, data=self.data_3)
        reasons = SuspicionReasons.objects.filter(
            name__in=self.data_3.get('reasons')
            )
        self.assertEqual(response.status_code, 200)
        self.changeset.refresh_from_db()
        self.assertEqual(
            self.changeset.new_features,
            [{
                "osm_id": 87765444,
                "url": "node-87765444",
                "version": 44,
                "reasons": [i.id for i in reasons]
            }]
            )
        self.assertEqual(
            Changeset.objects.get(id=self.data_3.get('changeset')).reasons.count(),
            1
            )

    def test_add_same_feature_twice(self):
        """If a feature with the same url is added twice, it should add the
        suspicion reason to the existing feature.
        """
        self.client.login(username=self.staff_user.username, password='password')
        response = self.client.post(self.url, data=self.data_3)
        self.assertEqual(response.status_code, 200)

        self.data_3['reasons'] = ["Relevant object deleted"]
        response = self.client.post(self.url, data=self.data_3)
        self.assertEqual(response.status_code, 200)
        self.changeset.refresh_from_db()
        self.assertEqual(len(self.changeset.new_features), 1)
        self.assertEqual(self.changeset.new_features[0]['osm_id'], 87765444)
        self.assertIn(
            SuspicionReasons.objects.get(name="Deleted Motorway").id,
            self.changeset.new_features[0]['reasons']
            )
        self.assertIn(
            SuspicionReasons.objects.get(name="Relevant object deleted").id,
            self.changeset.new_features[0]['reasons']
            )
        self.assertEqual(
            Changeset.objects.get(id=self.data_3.get('changeset')).reasons.count(),
            2
            )

    def test_validation(self):
        self.client.login(username=self.staff_user.username, password='password')
        # validate osm_id
        payload = {
            "osm_id": "asdfs",
            "changeset": 1234,
            "osm_type": "node",
            "version": 54,
            "name": "Tall Building",
            "reasons": ["Other reason"],
            }
        response = self.client.post(self.url, data=payload)
        self.assertEqual(response.status_code, 400)
        # validate changeset
        payload = {
            "osm_id": 12312,
            "changeset": "123-32",
            "osm_type": "node",
            "version": 54,
            "name": "Tall Building",
            "reasons": ["Other reason"],
            }
        response = self.client.post(self.url, data=payload)
        self.assertEqual(response.status_code, 400)
        # validate osm_type
        payload = {
            "osm_id": 12312,
            "changeset": 1234,
            "osm_type": "area",
            "version": 54,
            "name": "Tall Building",
            "reasons": ["Other reason"],
            }
        response = self.client.post(self.url, data=payload)
        self.assertEqual(response.status_code, 400)
