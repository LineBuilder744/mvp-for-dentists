from django.contrib.auth.backends import ModelBackend


class VerifiedEmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username:
            username = username.strip().lower()
        if kwargs.get("email"):
            kwargs["email"] = kwargs["email"].strip().lower()
        return super().authenticate(request, username=username, password=password, **kwargs)

    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and user.email_verified
