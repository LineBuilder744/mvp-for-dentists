from django.urls import path
from . import views

app_name = "orders"
urlpatterns = [
    path("", views.order_list, name="list"),
    path("new/", views.order_create, name="create"),
    path("quote/", views.order_quote, name="quote"),
    path("<int:pk>/", views.order_detail, name="detail"),
    path("<int:pk>/comments/", views.add_comment, name="comment"),
    path("<int:pk>/files/", views.add_file, name="add_file"),
    path("<int:pk>/<str:action>/", views.client_action, name="action"),
    path("files/<int:pk>/download/", views.download_file, name="download"),
]
