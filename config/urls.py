from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from shares import views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Public (acheteur)
    path("", views.home, name="home"),
    path("s/<str:code>/", views.view_share, name="view_share"),
    path("s/<str:code>/f/<int:file_id>/", views.serve_shared_file, name="shared_file"),
    # Comptes
    path("compte/inscription/", views.register, name="register"),
    path("compte/connexion/", auth_views.LoginView.as_view(), name="login"),
    path("compte/deconnexion/", auth_views.LogoutView.as_view(), name="logout"),
    # Espace vendeur
    path("mes-dossiers/", views.dashboard, name="dashboard"),
    path("mes-dossiers/nouveau/", views.create_share, name="create_share"),
    path("mes-dossiers/<int:pk>/", views.share_detail, name="share_detail"),
    path("mes-dossiers/<int:pk>/prolonger/", views.extend_share, name="extend_share"),
    path("mes-dossiers/<int:pk>/supprimer/", views.delete_share, name="delete_share"),
    path("mes-dossiers/<int:pk>/f/<int:file_id>/", views.owner_preview_file, name="owner_file"),
    path("mes-dossiers/<int:pk>/f/<int:file_id>/supprimer/", views.delete_share_file, name="delete_share_file"),
]
