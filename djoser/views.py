from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.template import loader
from rest_framework import generics, permissions, status, response
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from django.contrib.auth.tokens import default_token_generator
from . import serializers, settings, utils

User = get_user_model()


class SendEmailViewMixin(object):

    def send_email(self, to_email, from_email, context, subject_template_name,
                   plain_body_template_name, html_body_template_name=None):
        subject = loader.render_to_string(subject_template_name, context)
        subject = ''.join(subject.splitlines())
        body = loader.render_to_string(plain_body_template_name, context)
        email_message = EmailMultiAlternatives(subject, body, from_email, [to_email])
        if html_body_template_name is not None:
            html_email = loader.render_to_string(html_body_template_name, context)
            email_message.attach_alternative(html_email, 'text/html')
        email_message.send()

    def get_send_email_kwargs(self, user):
        return {
            'from_email': getattr(django_settings, 'DEFAULT_FROM_EMAIL', None),
            'to_email': user.email,
            'context': self.get_email_context(user),
        }

    def get_email_context(self, user):
        token = self.token_generator.make_token(user)
        uid = utils.encode_uid(user.pk)
        url = settings.get('ACTIVATION_URL').format(uid=uid, token=token)
        return {
            'user': user,
            'domain': settings.get('DOMAIN'),
            'site_name': settings.get('SITE_NAME'),
            'url': url,
            'uid': uid,
            'token': token,
            'protocol': 'https' if self.request.is_secure() else 'http',
        }


class PostActionViewMixin(object):

    def post(self, request):
        serializer = self.get_serializer(data=request.DATA)
        if serializer.is_valid():
            return self.action(serializer)
        else:
            return response.Response(
                data=serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )


class RegistrationView(SendEmailViewMixin, generics.CreateAPIView):
    permission_classes = (
        permissions.AllowAny,
    )
    token_generator = default_token_generator

    def get_serializer_class(self):
        if settings.get('LOGIN_AFTER_REGISTRATION'):
            return serializers.UserRegistrationWithAuthTokenSerializer
        return serializers.UserRegistrationSerializer

    def post_save(self, obj, created=False):
        if settings.get('LOGIN_AFTER_REGISTRATION'):
            Token.objects.get_or_create(user=obj)
        if settings.get('SEND_ACTIVATION_EMAIL'):
            self.send_email(**self.get_send_email_kwargs(obj))

    def get_send_email_kwargs(self, user):
        context = super(RegistrationView, self).get_send_email_kwargs(user)
        context.update({
            'subject_template_name': 'activation_email_subject.txt',
            'plain_body_template_name': 'activation_email_body.txt',
        })
        return context

    def get_email_context(self, user):
        context = super(RegistrationView, self).get_email_context(user)
        context['url'] = settings.get('ACTIVATION_URL').format(**context)
        return context


class LoginView(PostActionViewMixin, generics.GenericAPIView):
    serializer_class = serializers.UserLoginSerializer
    permission_classes = (
        permissions.AllowAny,
    )

    def action(self, serializer):
        token, _ = Token.objects.get_or_create(user=serializer.object)
        return Response(
            data=serializers.TokenSerializer(token).data,
            status=status.HTTP_200_OK,
        )


class PasswordResetView(SendEmailViewMixin, generics.GenericAPIView):
    serializer_class = serializers.PasswordResetSerializer
    permission_classes = (
        permissions.AllowAny,
    )
    token_generator = default_token_generator

    def post(self, request):
        serializer = self.get_serializer(data=request.DATA)
        if serializer.is_valid():
            for user in self.get_users(serializer.data['email']):
                self.send_email(**self.get_send_email_kwargs(user))
            return response.Response(status=status.HTTP_200_OK)
        else:
            return response.Response(
                data=serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

    def get_users(self, email):
        active_users = User._default_manager.filter(
            email__iexact=email,
            is_active=True,
        )
        return (u for u in active_users if u.has_usable_password())

    def get_send_email_kwargs(self, user):
        context = super(PasswordResetView, self).get_send_email_kwargs(user)
        context.update({
            'subject_template_name': 'password_reset_email_subject.txt',
            'plain_body_template_name': 'password_reset_email_body.txt',
        })
        return context

    def get_email_context(self, user):
        context = super(PasswordResetView, self).get_email_context(user)
        context['url'] = settings.get('PASSWORD_RESET_CONFIRM_URL').format(**context)
        return context


class SetPasswordView(PostActionViewMixin, generics.GenericAPIView):
    permission_classes = (
        permissions.IsAuthenticated,
    )

    def get_serializer_class(self):
        if settings.get('SET_PASSWORD_RETYPE'):
            return serializers.SetPasswordRetypeSerializer
        return serializers.SetPasswordSerializer

    def action(self, serializer):
        self.request.user.set_password(serializer.data['new_password'])
        self.request.user.save()
        return response.Response(status=status.HTTP_200_OK)


class PasswordResetConfirmView(PostActionViewMixin, generics.GenericAPIView):
    permission_classes = (
        permissions.AllowAny,
    )
    token_generator = default_token_generator

    def get_serializer_class(self):
        if settings.get('PASSWORD_RESET_CONFIRM_RETYPE'):
            return serializers.PasswordResetConfirmRetypeSerializer
        return serializers.PasswordResetConfirmSerializer

    def action(self, serializer):
        serializer.user.set_password(serializer.data['new_password'])
        serializer.user.save()
        return response.Response(status=status.HTTP_200_OK)


class ActivationView(PostActionViewMixin, generics.GenericAPIView):
    serializer_class = serializers.UidAndTokenSerializer
    permission_classes = (
        permissions.AllowAny,
    )
    token_generator = default_token_generator

    def action(self, serializer):
        serializer.user.is_active = True
        serializer.user.save()
        if settings.get('LOGIN_AFTER_ACTIVATION'):
            token, _ = Token.objects.get_or_create(user=serializer.user)
            data = serializers.TokenSerializer(token).data
        else:
            data = {}
        return Response(data=data, status=status.HTTP_200_OK)


class SetUsernameView(PostActionViewMixin, generics.GenericAPIView):
    serializer_class = serializers.SetUsernameSerializer
    permission_classes = (
        permissions.IsAuthenticated,
    )

    def get_serializer_class(self):
        if settings.get('SET_USERNAME_RETYPE'):
            return serializers.SetUsernameRetypeSerializer
        return serializers.SetUsernameSerializer

    def action(self, serializer):
        setattr(self.request.user, User.USERNAME_FIELD, serializer.data['new_' + User.USERNAME_FIELD])
        self.request.user.save()
        return response.Response(status=status.HTTP_200_OK)