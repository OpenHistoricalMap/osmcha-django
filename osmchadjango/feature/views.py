import json
from datetime import datetime

from django.views.generic import View, ListView
from django.views.generic.detail import SingleObjectMixin
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render
from django.http import JsonResponse
from django.core.urlresolvers import reverse
from django.utils import timezone
from django.utils.translation import ugettext, ugettext_lazy as _
from django.db import IntegrityError
from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.core.exceptions import ValidationError
from django.contrib.gis.gdal.error import GDALException

import django_filters.rest_framework
from rest_framework.generics import (
    ListAPIView, RetrieveAPIView, get_object_or_404
    )
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework import status
from rest_framework_gis.filters import InBBoxFilter
from rest_framework_gis.pagination import GeoJsonPagination

from osmchadjango.changeset import models as changeset_models

from .models import Feature
from .serializers import FeatureSerializer
from .filters import FeatureFilter


class StandardResultsSetPagination(GeoJsonPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500


class FeatureListAPIView(ListAPIView):
    queryset = Feature.objects.all()
    serializer_class = FeatureSerializer
    pagination_class = StandardResultsSetPagination
    bbox_filter_field = 'geometry'
    filter_backends = (
        InBBoxFilter,
        django_filters.rest_framework.DjangoFilterBackend,
        )
    bbox_filter_include_overlapping = True
    filter_class = FeatureFilter


class FeatureListView(ListView):
    context_object_name = 'features'
    template_name = 'feature/feature_list.html'
    paginate_by = 15

    def get_context_data(self, **kwargs):
        context = super(FeatureListView, self).get_context_data(**kwargs)
        suspicion_reasons = changeset_models.SuspicionReasons.objects.all()
        get = self.request.GET.dict()
        if 'harmful' not in get:
            get['harmful'] = 'False'
        if 'checked' not in get:
            get['checked'] = 'All'
        sorts = {
            '-date': 'Recent First',
            '-delete': 'Most Deletions First',
            '-modify': 'Most Modifications First',
            '-create': 'Most Creations First'
            }
        context.update({
            'suspicion_reasons': suspicion_reasons,
            'get': get,
            'sorts': sorts
            })
        return context

    def get_queryset(self):
        queryset = Feature.objects.all().select_related('changeset')
        params = {}
        GET_dict = self.request.GET.dict()
        for key in GET_dict:
            if key in GET_dict and GET_dict[key] != '':
                params[key] = GET_dict[key]

        self.validate_params(params)

        if 'harmful' not in params:
            params['harmful'] = 'False'
        if 'checked' not in params:
            params['checked'] = 'All'
        if 'reasons' in params:
            if params['reasons'] == 'None':
                queryset = queryset.filter(reasons=None)
            else:
                queryset = queryset.filter(reasons=int(params['reasons']))
        if 'bbox' in params:
            bbox = Polygon.from_bbox((float(b) for b in params['bbox'].split(',')))
            queryset = queryset.filter(changeset__bbox__bboverlaps=bbox)

        queryset = FeatureFilter(params, queryset=queryset).qs

        if 'sort' in GET_dict and GET_dict['sort'] != '':
            queryset = queryset.order_by(GET_dict['sort'])
        else:
            queryset = queryset.order_by('-changeset__date')
        return queryset

    def validate_params(self, params):
        '''FIXME: define error in except lines.'''
        if 'reasons' in params.keys() and params['reasons'] != '':
            try:
                s = str(int(params['reasons']))
            except:
                raise ValidationError('reasons param must be a number')
        if 'bbox' in params.keys() and params['bbox'] != '':
            try:
                bbox = Polygon.from_bbox((float(b) for b in params['bbox'].split(',')))
            except:
                raise ValidationError('bbox param is invalid')


class FeatureDetailAPIView(RetrieveAPIView):
    '''DetailView of Feature Model'''
    queryset = Feature.objects.all()
    serializer_class = FeatureSerializer

    def get_object(self):
        changeset = self.kwargs['changeset']
        url = self.kwargs['slug']
        return get_object_or_404(Feature, changeset=changeset, url=url)


def get_geojson(request, changeset, slug):
    feature = get_object_or_404(Feature, url=slug)
    return JsonResponse(feature.geojson)


@api_view(['POST'])
@parser_classes((JSONParser, MultiPartParser, FormParser))
@permission_classes((IsAuthenticated, IsAdminUser))
def create_feature(request):
    '''Create Suspicion Features. It was designed to receive vandalism-dynamosm
    json output.
    '''
    feature = request.data

    if 'properties' not in feature.keys():
        return Response(
            {'message': 'Expecting a single GeoJSON feature.'},
            status=status.HTTP_400_BAD_REQUEST
            )
    properties = feature.get('properties', {})
    changeset_id = properties.get('osm:changeset')

    if not changeset_id:
        return Response(
            {'message': 'osm:changeset field is missing.'},
            status=status.HTTP_400_BAD_REQUEST
            )

    # Each changed feature should have a 'suspicions' array of objects in its properties
    suspicions = properties.get('suspicions')
    reasons_texts = set()
    if suspicions:
        [reasons_texts.add(suspicion['reason']) for suspicion in suspicions]

    reasons = set()
    for reason_text in reasons_texts:
        reason, created = changeset_models.SuspicionReasons.objects.get_or_create(
            name=reason_text
            )
        reasons.add(reason)

    feature['properties'].pop('suspicions')

    defaults = {
        'date': datetime.utcfromtimestamp(properties.get('osm:timestamp') / 1000),
        'uid': properties.get('osm:uid'),
        'is_suspect': True
        }

    changeset, created = changeset_models.Changeset.objects.get_or_create(
        id=changeset_id,
        defaults=defaults
        )

    if not changeset.is_suspect:
        changeset.is_suspect = True
        changeset.save()

    try:
        changeset.reasons.add(*reasons)
    except IntegrityError:
        # This most often happens due to a race condition,
        # where two processes are saving to the same changeset
        # In this case, we can safely ignore this attempted DB Insert,
        # since what we wanted inserted has already been done through
        # a separate web request.
        print('IntegrityError with changeset %s' % changeset_id)
    except ValueError as e:
        print('ValueError with changeset %s' % changeset_id)

    defaults = {
        'osm_id': properties['osm:id'],
        'osm_type': properties['osm:type'],
        'url': '{}-{}'.format(properties['osm:type'], properties['osm:id']),
        'osm_version': properties['osm:version'],
        'comparator_version': feature.get('comparator_version'),
        }

    try:
        defaults['geometry'] = GEOSGeometry(json.dumps(feature['geometry']))
    except (GDALException, ValueError, TypeError) as e:
        return Response(
            {'message': '{} in geometry field of feature {}'.format(e, properties['osm:id'])},
            status=status.HTTP_400_BAD_REQUEST
            )

    if 'oldVersion' in properties.keys():
        try:
            defaults['old_geometry'] = GEOSGeometry(
                json.dumps(properties['oldVersion']['geometry'])
                )
        except (GDALException, ValueError, TypeError) as e:
            print(
                '{} in oldVersion.geometry field of feature {}'.format(
                    e, properties['osm:id']
                    )
                )
        defaults['old_geojson'] = feature['properties'].pop('oldVersion')

    defaults['geojson'] = feature
    suspicious_feature, created = Feature.objects.get_or_create(
        osm_id=properties['osm:id'],
        changeset=changeset,
        defaults=defaults
        )

    try:
        suspicious_feature.reasons.add(*reasons)
    except IntegrityError:
        # This most often happens due to duplicates in dynamosm stream
        print('Integrity error with feature %s' % suspicious_feature.osm_id)
    except ValueError as e:
        print('Value error with feature %s' % suspicious_feature.osm_id)

    return Response(
        {'message': 'Feature created.'},
        status=status.HTTP_201_CREATED
        )


class SetHarmfulFeature(SingleObjectMixin, View):
    model = Feature

    def get_object(self):
        changeset = self.kwargs['changeset']
        url = self.kwargs['slug']

        return get_object_or_404(Feature, changeset=changeset, url=url)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.changeset.uid not in [i.uid for i in request.user.social_auth.all()]:
            return render(
                request,
                'feature/confirm_modify.html',
                {'feature': self.object, 'modification': _('harmful')}
                )
        else:
            return render(request, 'feature/not_allowed.html')

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.changeset.uid not in [i.uid for i in request.user.social_auth.all()]:
            self.object.checked = True
            self.object.harmful = True
            self.object.check_user = request.user
            self.object.check_date = timezone.now()
            self.object.save()
            return HttpResponseRedirect(
                reverse(
                    'feature:detail',
                    args=[self.object.changeset, self.object.url]
                    )
                )
        else:
            return render(request, 'feature/not_allowed.html')


@csrf_exempt
def whitelist_user(request):
    '''View to mark a user as whitelisted.
    Accepts a single post parameter with the 'name' of the user to be white-listed.
    Whitelists that user for the currently logged in user.
    TODO: can this be converted to a CBV?
    '''
    name = request.POST.get('name', None)
    user = request.user
    if not user.is_authenticated():
        return JsonResponse({'error': 'Not authenticated'}, status=401)
    if not name:
        return JsonResponse({'error': 'Needs name parameter'}, status=403)
    uw = changeset_models.UserWhitelist(user=user, whitelist_user=name)
    uw.save()
    return JsonResponse({'success': 'User %s has been white-listed' % name})


class SetGoodFeature(SingleObjectMixin, View):
    model = Feature

    def get_object(self):
        changeset = self.kwargs['changeset']
        url = self.kwargs['slug']

        return get_object_or_404(Feature, changeset=changeset, url=url)

    def get(self, request, *args, **kwargs):
        if self.object.changeset.uid not in [i.uid for i in request.user.social_auth.all()]:
            return render(
                request,
                'feature/confirm_modify.html',
                {'feature': self.object, 'modification': _('good')}
                )
        else:
            return render(request, 'feature/not_allowed.html')

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.changeset.uid not in [i.uid for i in request.user.social_auth.all()]:
            self.object.checked = True
            self.object.harmful = False
            self.object.check_user = request.user
            self.object.check_date = timezone.now()
            self.object.save()
            return HttpResponseRedirect(
                reverse('feature:detail', args=[self.object.changeset, self.object.url])
                )
        else:
            return render(request, 'feature/not_allowed.html')
