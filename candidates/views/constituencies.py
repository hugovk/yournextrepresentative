from datetime import timedelta

from slugify import slugify

from django.views.decorators.cache import cache_control
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.utils.decorators import method_decorator
from django.utils.http import urlquote
from django.utils.translation import ugettext as _
from django.views.generic import TemplateView, FormView, View
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Prefetch

from elections.mixins import ElectionMixin
from auth_helpers.views import GroupRequiredMixin
from .helpers import (
    get_party_people_for_election_from_memberships,
    split_candidacies, get_redirect_to_post,
    group_candidates_by_party
)
from .version_data import get_client_ip, get_change_metadata
from ..csv_helpers import list_to_csv
from ..forms import NewPersonForm, ToggleLockForm, ConstituencyRecordWinnerForm
from ..models import (
    TRUSTED_TO_LOCK_GROUP_NAME, get_edits_allowed,
    RESULT_RECORDERS_GROUP_NAME, LoggedAction, PostExtra, OrganizationExtra,
    MembershipExtra, PartySet
)
from official_documents.models import OfficialDocument
from results.models import ResultEvent

from popolo.models import Membership, Post, Organization, Person

class ConstituencyDetailView(ElectionMixin, TemplateView):
    template_name = 'candidates/constituency.html'

    @method_decorator(cache_control(max_age=(60 * 20)))
    def dispatch(self, *args, **kwargs):
        return super(ConstituencyDetailView, self).dispatch(
            *args, **kwargs
        )

    def get_context_data(self, **kwargs):
        from ..election_specific import shorten_post_label
        context = super(ConstituencyDetailView, self).get_context_data(**kwargs)

        context['post_id'] = post_id = kwargs['post_id']
        mp_post = Post.objects.get(extra__slug=post_id)

        documents_by_type = {}
        # Make sure that every available document type has a key in
        # the dictionary, even if there are no such documents.
        doc_lookup = {t[0]: (t[1], t[2]) for t in OfficialDocument.DOCUMENT_TYPES}
        for t in doc_lookup.values():
            documents_by_type[t] = []
        documents_for_post = OfficialDocument.objects.filter(post_id=mp_post.id)
        for od in documents_for_post:
            documents_by_type[doc_lookup[od.document_type]].append(od)
        context['official_documents'] = documents_by_type.items()
        context['some_official_documents'] = documents_for_post.count()

        context['post_label'] = mp_post.label
        context['post_label_shorter'] = shorten_post_label(context['post_label'])

        context['redirect_after_login'] = \
            urlquote(reverse('constituency', kwargs={
                'election': self.election,
                'post_id': post_id,
                'ignored_slug': slugify(context['post_label_shorter'])
            }))

        context['post_data'] = {
            'id': mp_post.extra.slug,
            'label': mp_post.label
        }

        context['candidates_locked'] = False
        if hasattr(mp_post, 'extra'):
            context['candidates_locked'] = mp_post.extra.candidates_locked

        context['lock_form'] = ToggleLockForm(
            initial={
                'post_id': post_id,
                'lock': not context['candidates_locked'],
            },
        )
        context['candidate_list_edits_allowed'] = \
            get_edits_allowed(self.request.user, context['candidates_locked'])

        extra_qs = MembershipExtra.objects.select_related('election')
        current_candidacies, past_candidacies = \
            split_candidacies(
                self.election_data,
                mp_post.memberships.prefetch_related(
                    Prefetch('extra', queryset=extra_qs)
                ).select_related('person', 'on_behalf_of', 'organization').all()
            )

        current_candidates = set(c.person for c in current_candidacies)
        past_candidates = set(c.person for c in past_candidacies)

        other_candidates = past_candidates - current_candidates
        other_candidacies = [c for c in past_candidacies
                             if c.person in other_candidates]

        # Now split those candidates into those that we know aren't
        # standing again, and those that we just don't know about.
        """ need to create some sort of anti candidate for this to work
        context['candidates_not_standing_again'] = \
            group_people_by_party(
                self.election,
                set(p for p in other_candidates if
                    p.memberships
                    .filter(
                        role=self.election_data.noncandidate_membership_role,
                        extra__election__slug=self.election)
                    .count() != 0),
                party_list=self.election_data.party_lists_in_use,
                max_people=self.election_data.default_party_list_members_to_show
            )
            """

        context['candidacies_might_stand_again'] = \
            group_candidates_by_party(
                self.election_data,
                other_candidacies,
                party_list=self.election_data.party_lists_in_use,
                max_people=self.election_data.default_party_list_members_to_show
            )

        context['candidacies'] = group_candidates_by_party(
            self.election_data,
            current_candidacies,
            party_list=self.election_data.party_lists_in_use,
            max_people=self.election_data.default_party_list_members_to_show
        )

        context['show_retract_result'] = False
        if Membership.objects.filter(
            role=self.election_data.winner_membership_role,
            extra__election__slug=self.election,
            post=mp_post
            ).count() == 1:
            context['show_retract_result'] = True

        context['show_confirm_result'] = not context['show_retract_result']

        context['add_candidate_form'] = NewPersonForm(
            election=self.election,
            initial={
                ('constituency_' + self.election): post_id,
                ('standing_' + self.election): 'standing',
            },
            hidden_post_widget=True,
        )

        return context


class ConstituencyDetailCSVView(ConstituencyDetailView):
    def render_to_response(self, context, **response_kwargs):
        all_people = [
            person.as_dict(self.election)
            for person in context['candidates']
        ]
        filename = "{election}-{constituency_slug}.csv".format(
            election=self.election,
            constituency_slug=slugify(context['constituency_name']),
        )
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="%s"' % filename
        response.write(list_to_csv(all_people))
        return response


class ConstituencyListView(ElectionMixin, TemplateView):
    template_name = 'candidates/constituencies.html'

    def get_context_data(self, **kwargs):
        context = super(ConstituencyListView, self).get_context_data(**kwargs)
        context['all_constituencies'] = \
            PostExtra.objects.filter(
                elections__slug=self.election
            ).order_by('base__label').select_related('base')

        return context


class ConstituencyLockView(ElectionMixin, GroupRequiredMixin, View):
    required_group_name = TRUSTED_TO_LOCK_GROUP_NAME

    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        form = ToggleLockForm(data=self.request.POST)
        if form.is_valid():
            post_id = form.cleaned_data['post_id']
            with transaction.atomic():
                post = get_object_or_404(Post, extra__slug=post_id)
                lock = form.cleaned_data['lock']
                post.extra.candidates_locked = lock
                post.extra.save()
                post_name = post.extra.short_label
                if lock:
                    suffix = '-lock'
                    pp = 'Locked'
                else:
                    suffix = '-unlock'
                    pp = 'Unlocked'
                message = pp + u' constituency {0} ({1})'.format(
                    post_name, post.id
                )

                LoggedAction.objects.create(
                    user=self.request.user,
                    action_type=('constituency' + suffix),
                    ip_address=get_client_ip(self.request),
                    source=message,
                )
            return HttpResponseRedirect(
                reverse('constituency', kwargs={
                    'election': self.election,
                    'post_id': post_id,
                    'ignored_slug': slugify(post_name),
                })
            )
        else:
            message = _('Invalid data POSTed to ConstituencyLockView')
            raise ValidationError(message)


class ConstituenciesUnlockedListView(ElectionMixin, TemplateView):
    template_name = 'candidates/constituencies-unlocked.html'

    def get_context_data(self, **kwargs):
        context = super(ConstituenciesUnlockedListView, self).get_context_data(**kwargs)
        total_constituencies = 0
        total_locked = 0
        keys = ('locked', 'unlocked')
        for k in keys:
            context[k] = []
        posts = Post.objects.select_related('extra').all()
        for post in posts:
            total_constituencies += 1
            if post.extra.candidates_locked:
                context_field = 'locked'
                total_locked += 1
            else:
                context_field = 'unlocked'
            context[context_field].append(
                {
                    'id': post.id,
                    'name': post.extra.short_label,
                }
            )
        for k in keys:
            context[k].sort(key=lambda c: c['name'])
        context['total_constituencies'] = total_constituencies
        context['total_left'] = total_constituencies - total_locked
        context['percent_done'] = (100 * total_locked) / total_constituencies
        return context

class ConstituencyRecordWinnerView(ElectionMixin, GroupRequiredMixin, FormView):

    form_class = ConstituencyRecordWinnerForm
    template_name = 'candidates/record-winner.html'
    required_group_name = RESULT_RECORDERS_GROUP_NAME

    def dispatch(self, request, *args, **kwargs):
        person_id = self.request.POST.get(
            'person_id',
            self.request.GET.get('person', '')
        )
        self.person = get_object_or_404(Person, id=person_id)
        self.post_data = get_object_or_404(Post, extra__slug=self.kwargs['post_id'])

        return super(ConstituencyRecordWinnerView, self). \
            dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super(ConstituencyRecordWinnerView, self). \
            get_initial()
        initial['person_id'] = self.person.id
        return initial

    def get_context_data(self, **kwargs):
        context = super(ConstituencyRecordWinnerView, self). \
            get_context_data(**kwargs)
        context['post_id'] = self.kwargs['post_id']
        context['constituency_name'] = self.post_data.label
        context['person'] = self.person
        return context

    def form_valid(self, form):
        change_metadata = get_change_metadata(
            self.request,
            form.cleaned_data['source']
        )

        with transaction.atomic():
            existing_winner = self.post_data.memberships.filter(
                extra__elected=True,
                extra__election=self.election_data
            )
            # FIXME: need to fix for elections with multiple winners
            if existing_winner.exists():
                old_winner = existing_winner.first()
                old_winner.person.extra.record_version(change_metadata)
                old_winner.person.save()

                LoggedAction.objects.create(
                    user=self.request.user,
                    action_type='set-candidate-not-elected',
                    ip_address=get_client_ip(self.request),
                    popit_person_new_version=change_metadata['version_id'],
                    person=old_winner.person,
                    source=change_metadata['information_source'],
                )

                old_winner.extra.elected = False
                old_winner.save()

            role = self.election_data.candidate_membership_role
            membership = Membership.objects.get(
                role=role,
                post=self.post_data,
                person=self.person,
                extra__election=self.election_data,
            )

            membership.extra.elected = True
            membership.extra.save()

            all_candidates = self.post_data.memberships.filter(
                role=self.election_data.candidate_membership_role,
                extra__election=self.election_data,
            ).exclude(
                extra__elected=True
            )

            for candidate in all_candidates.all():
                candidate.extra.elected = False
                candidate.extra.save()


            ResultEvent.objects.create(
                election=self.election_data,
                winner=self.person,
                winner_person_name=self.person.name,
                post_id=self.post_data.extra.slug,
                post_name=self.post_data.label,
                winner_party_id=membership.on_behalf_of.extra.slug,
                source=form.cleaned_data['source'],
                user=self.request.user,
            )

            self.person.extra.record_version(change_metadata)
            self.person.save()

            LoggedAction.objects.create(
                user=self.request.user,
                action_type='set-candidate-elected',
                ip_address=get_client_ip(self.request),
                popit_person_new_version=change_metadata['version_id'],
                person=self.person,
                source=change_metadata['information_source'],
            )

        return get_redirect_to_post(
            self.election,
            self.post_data,
        )


class ConstituencyRetractWinnerView(ElectionMixin, GroupRequiredMixin, View):

    required_group_name = RESULT_RECORDERS_GROUP_NAME
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        post_id = self.kwargs['post_id']
        post = get_object_or_404(Post, extra__slug=post_id)
        constituency_name = post.extra.short_label

        with transaction.atomic():
            existing_winner = post.memberships.filter(
                extra__elected=True,
                extra__election=self.election_data
            )

            if existing_winner.exists():
                membership = existing_winner.first()
                candidate = membership.person

                change_metadata = get_change_metadata(
                    self.request,
                    _('Result recorded in error, retracting')
                )
                candidate.extra.record_version(change_metadata)
                candidate.save()
                LoggedAction.objects.create(
                    user=self.request.user,
                    action_type='retract-result',
                    ip_address=get_client_ip(self.request),
                    popit_person_new_version=change_metadata['version_id'],
                    person=candidate,
                    source=change_metadata['information_source'],
                )

            all_candidates = self.post_data.memberships.filter(
                role=self.election_data.candidate_membership_role,
                extra__election=self.election_data,
            )
            for candidate in all_candidates.all():
                candidate.extra.elected = None
                candidate.extra.save()

        return HttpResponseRedirect(
            reverse(
                'constituency',
                kwargs={
                    'post_id': post_id,
                    'election': self.election,
                    'ignored_slug': slugify(constituency_name),
                }
            )
        )


def memberships_contain_winner(memberships, election_data):
    for m in memberships.all():
        correct_org = m.organization == election_data.organization
        day_after_election = election_data.election_date + timedelta(days=1)
        correct_start_date = m.start_date == str(day_after_election)
        if correct_org and correct_start_date:
            return True
    return False


class ConstituenciesDeclaredListView(ElectionMixin, TemplateView):
    template_name = 'candidates/constituencies-declared.html'

    def get_context_data(self, **kwargs):
        context = super(ConstituenciesDeclaredListView, self).get_context_data(**kwargs)
        total_constituencies = 0
        total_declared = 0
        constituency_declared = []
        constituency_seen = {}
        constituencies = []
        total_constituencies = Post.objects.all().count()
        for membership in Membership.objects.select_related('post', 'post__area').filter(
            post__isnull=False,
            organization_id=self.election_data.organization_id,
            extra__election_id=self.election_data.id,
            role=''
        ):
            constituency_declared.append(membership.post.id)
            total_declared += 1
            constituencies.append((membership.post, True))
        for membership in Membership.objects.select_related('post', 'post__area').filter(
            post__isnull=False,
            extra__election=self.election_data,
            role=self.election_data.candidate_membership_role
        ).exclude(post_id__in=constituency_declared):
            if constituency_seen.get(membership.post.id, False):
                continue
            constituency_seen[membership.post.id] = True
            constituencies.append((membership.post, False))
        constituencies.sort(key=lambda c: c[0].area.name)
        context['constituencies'] = constituencies
        context['total_constituencies'] = total_constituencies
        context['total_left'] = total_constituencies - total_declared
        context['percent_done'] = (100 * total_declared) / total_constituencies
        return context


class OrderedPartyListView(ElectionMixin, TemplateView):
    template_name = 'candidates/ordered-party-list.html'

    @method_decorator(cache_control(max_age=(60 * 20)))
    def dispatch(self, *args, **kwargs):
        return super(OrderedPartyListView, self).dispatch(
            *args, **kwargs
        )

    def get_context_data(self, **kwargs):
        from ..election_specific import shorten_post_label
        context = super(OrderedPartyListView, self).get_context_data(**kwargs)

        context['post_id'] = post_id = kwargs['post_id']
        mp_post = get_object_or_404(Post, extra__slug=post_id)

        party_id = kwargs['organization_id']
        party_extra = OrganizationExtra.objects.get(
            slug=party_id
        ).select_related('base')
        party_name = party_extra.base.name
        if not party_name:
            raise Http404(_("Party '{party_id}' not found").format(
                party_id=party_id)
            )

        party = get_object_or_404(Organization, extra__slug=party_id)
        context['party'] = party

        context['post_label'] = mp_post.label
        context['post_label_shorter'] = shorten_post_label(context['post_label'])

        context['redirect_after_login'] = \
            urlquote(reverse('party-for-post', kwargs={
                'election': self.election,
                'post_id': post_id,
                'organization_id': party_id
            }))

        context['post_data'] = {
            'id': mp_post.id,
            'label': mp_post.label
        }

        context['candidates_locked'] = mp_post.extra.candidates_locked
        context['lock_form'] = ToggleLockForm(
            initial={
                'post_id': post_id,
                'lock': not context['candidates_locked'],
            },
        )
        context['candidate_list_edits_allowed'] = \
            get_edits_allowed(self.request.user, context['candidates_locked'])

        context['positions_and_people'] = \
            get_party_people_for_election_from_memberships(
                self.election, party.id, mp_post.memberships
            )

        party_set = PartySet.objects.get(postextra__base__id=post_id)

        context['add_candidate_form'] = NewPersonForm(
            election=self.election,
            initial={
                ('constituency_' + self.election): post_id,
                ('standing_' + self.election): 'standing',
                ('party_' + party_set.slug + '_' + self.election): party_id,
            },
            hidden_post_widget=True,
        )

        return context
