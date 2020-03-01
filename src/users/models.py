import logging

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db import models
from django.db.models import Q
from django.template.loader import get_template
from django.utils.encoding import python_2_unicode_compatible

from app.utils import create_hash
from environments.models import UserEnvironmentPermission, UserPermissionGroupEnvironmentPermission, Environment, \
    Identity
from organisations.models import Organisation, UserOrganisation, OrganisationRole, organisation_roles
from projects.models import UserProjectPermission, UserPermissionGroupProjectPermission, Project
from users.exceptions import InvalidInviteError

logger = logging.getLogger(__name__)


class UserManager(BaseUserManager):
    """Define a model manager for User model with no username field."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        """Create and save a User with the given email and password."""
        if not email:
            raise ValueError('The given email must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        """Create and save a regular User with the given email and password."""
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        """Create and save a SuperUser with the given email and password."""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user(email, password, **extra_fields)


@python_2_unicode_compatible
class FFAdminUser(AbstractUser):
    organisations = models.ManyToManyField(Organisation, related_name="users", blank=True, through=UserOrganisation)
    email = models.EmailField(unique=True, null=False)
    objects = UserManager()
    username = models.CharField(
        unique=True,
        max_length=150,
        null=True,
        blank=True
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    class Meta:
        ordering = ['id']
        verbose_name = 'Feature flag admin user'

    def __str__(self):
        return "%s %s" % (self.first_name, self.last_name)

    def get_full_name(self):
        if not self.first_name:
            return None
        return ' '.join([self.first_name, self.last_name]).strip()

    def join_organisation(self, invite):
        organisation = invite.organisation

        if invite.email.lower() != self.email.lower():
            raise InvalidInviteError('Registered email does not match invited email')

        self.add_organisation(organisation, role=OrganisationRole(invite.role))
        invite.delete()

    def is_admin(self, organisation):
        return self.get_organisation_role(organisation) == OrganisationRole.ADMIN.name

    def get_admin_organisations(self):
        return Organisation.objects.filter(userorganisation__user=self,
                                           userorganisation__role=OrganisationRole.ADMIN.name)

    def add_organisation(self, organisation, role=OrganisationRole.USER):
        UserOrganisation.objects.create(user=self, organisation=organisation, role=role.name)

    def remove_organisation(self, organisation):
        UserOrganisation.objects.filter(user=self, organisation=organisation).delete()

    def get_organisation_role(self, organisation):
        user_organisation = self.get_user_organisation(organisation)
        if user_organisation:
            return user_organisation.role

    def get_organisation_join_date(self, organisation):
        user_organisation = self.get_user_organisation(organisation)
        if user_organisation:
            return user_organisation.date_joined

    def get_user_organisation(self, organisation):
        try:
            return self.userorganisation_set.get(organisation=organisation)
        except UserOrganisation.DoesNotExist:
            logger.warning('User %d is not part of organisation %d' % (self.id, organisation.id))

    def get_permitted_projects(self, permissions):
        """
        Get all projects that the user has the given permissions for.

        Rules:
            - User has the required permissions directly (UserProjectPermission)
            - User is in a UserPermissionGroup that has required permissions (UserPermissionGroupProjectPermissions)
            - User is an admin for the organisation the project belongs to
        """
        user_permission_query = Q()
        group_permission_query = Q()
        for permission in permissions:
            user_permission_query = user_permission_query & Q(userpermission__permissions__key=permission)
            group_permission_query = group_permission_query & Q(grouppermission__permissions__key=permission)

        user_query = Q(userpermission__user=self) & (user_permission_query | Q(userpermission__admin=True))
        group_query = Q(grouppermission__group__users=self) & (group_permission_query | Q(grouppermission__admin=True))
        organisation_query = Q(organisation__userorganisation__user=self,
                               organisation__userorganisation__role=OrganisationRole.ADMIN.name)

        query = (user_query | group_query | organisation_query)

        return Project.objects.filter(query).distinct()

    def has_project_permission(self, permission, project):
        if self.is_project_admin(project) or self.is_admin(project.organisation):
            return True

        return project in self.get_permitted_projects([permission])

    def is_project_admin(self, project):
        if self.is_admin(project.organisation):
            return True

        return UserProjectPermission.objects.filter(admin=True, user=self, project=project).exists() or \
               UserPermissionGroupProjectPermission.objects.filter(group__users=self, admin=True,
                                                                   project=project).exists()

    def get_permitted_environments(self, permissions):
        """
        Get all environments that the user has the given permissions for.

        Rules:
            - User has the required permissions directly (UserEnvironmentPermission)
            - User is in a UserPermissionGroup that has required permissions (UserPermissionGroupEnvironmentPermissions)
            - User is an admin for the organisation the environment belongs to
        """
        user_permission_query = Q()
        group_permission_query = Q()
        for permission in permissions:
            user_permission_query = user_permission_query & Q(userpermission__permissions__key=permission)
            group_permission_query = group_permission_query & Q(grouppermission__permissions__key=permission)

        user_query = Q(userpermission__user=self) & (user_permission_query | Q(userpermission__admin=True))
        group_query = Q(grouppermission__group__users=self) & (group_permission_query | Q(grouppermission__admin=True))
        organisation_query = Q(project__organisation__userorganisation__user=self,
                               project__organisation__userorganisation__role=OrganisationRole.ADMIN.name)
        project_admin_query = Q(project__userpermission__user=self, project__userpermission__admin=True) | Q(
            project__grouppermission__group__users=self, project__grouppermission__admin=True)

        query = (user_query | group_query | organisation_query | project_admin_query)

        return Environment.objects.filter(query).distinct()

    def get_permitted_identities(self):
        return Identity.objects.filter(
            environment__in=self.get_permitted_environments(permissions=['VIEW_ENVIRONMENT']))

    @staticmethod
    def send_alert_to_admin_users(subject, message):
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=FFAdminUser._get_admin_user_emails(),
            fail_silently=True
        )

    @staticmethod
    def _get_admin_user_emails():
        return [user['email'] for user in FFAdminUser.objects.filter(is_staff=True).values('email')]

    def belongs_to(self, organisation_id: int) -> bool:
        return organisation_id in self.organisations.all().values_list('id', flat=True)

    def is_environment_admin(self, environment):
        if self.is_admin(environment.project.organisation) or self.is_project_admin(environment.project):
            return True

        return UserEnvironmentPermission.objects.filter(admin=True, user=self, environment=environment).exists() or \
               UserPermissionGroupEnvironmentPermission.objects.filter(group__users=self, admin=True,
                                                                       environment=environment).exists()


@python_2_unicode_compatible
class Invite(models.Model):
    email = models.EmailField()
    hash = models.CharField(max_length=100, default=create_hash, unique=True)
    date_created = models.DateTimeField('DateCreated', auto_now_add=True)
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='invites')
    frontend_base_url = models.CharField(max_length=500, null=False)
    invited_by = models.ForeignKey(FFAdminUser, related_name='sent_invites', null=True, on_delete=models.CASCADE)
    role = models.CharField(choices=organisation_roles, max_length=50, default=OrganisationRole.USER.name)

    class Meta:
        unique_together = ('email', 'organisation')
        ordering = ['organisation', 'date_created']

    def save(self, *args, **kwargs):
        # send email invite before saving invite
        self.send_invite_mail()
        super(Invite, self).save(*args, **kwargs)

    def get_invite_uri(self):
        return self.frontend_base_url + str(self.hash)

    def send_invite_mail(self):
        context = {
            "org_name": self.organisation.name,
            "invite_url": self.get_invite_uri()
        }

        html_template = get_template('users/invite_to_org.html')
        plaintext_template = get_template('users/invite_to_org.txt')

        if self.invited_by:
            invited_by_name = self.invited_by.get_full_name()
            if not invited_by_name:
                invited_by_name = "A user"
            subject = settings.EMAIL_CONFIGURATION.get('INVITE_SUBJECT_WITH_NAME') % (
                invited_by_name, self.organisation.name
            )
        else:
            subject = settings.EMAIL_CONFIGURATION.get('INVITE_SUBJECT_WITHOUT_NAME') % \
                      self.organisation.name

        to = self.email

        text_content = plaintext_template.render(context)
        html_content = html_template.render(context)
        msg = EmailMultiAlternatives(
            subject,
            text_content,
            settings.EMAIL_CONFIGURATION.get('INVITE_FROM_EMAIL'),
            [to]
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()

    def __str__(self):
        return "%s %s" % (self.email, self.organisation.name)


class UserPermissionGroup(models.Model):
    """
    Model to group users within an organisation for the purposes of permissioning.
    """
    name = models.CharField(max_length=200)
    users = models.ManyToManyField('users.FFAdminUser', related_name='permission_groups')
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='permission_groups')

    def add_users_by_id(self, user_ids: list):
        users_to_add = []
        for user_id in user_ids:
            try:
                user = FFAdminUser.objects.get(id=user_id, organisations=self.organisation)
            except FFAdminUser.DoesNotExist:
                # re-raise exception with useful error message
                raise FFAdminUser.DoesNotExist('User %d does not exist in this organisation' % user_id)
            users_to_add.append(user)
        self.users.add(*users_to_add)

    def remove_users_by_id(self, user_ids: list):
        self.users.remove(*user_ids)
