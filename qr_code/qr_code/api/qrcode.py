import uuid

from django.http import Http404
from django.shortcuts import redirect
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from ..models import QRCode
from ..serializers import (
    QRCodeCreateSerializer,
    QRCodeSerializer,
    QRCodeUpdateSerializer,
)
from ..services import QRCodeGenerator


class QRCodeViewSet(viewsets.ModelViewSet):
    """ViewSet for QR Code operations."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter QR codes by the authenticated user, excluding soft-deleted items."""
        user = self.request.user
        if user.is_authenticated:
            return QRCode.objects.filter(created_by=user, deleted_at__isnull=True)
        return QRCode.objects.none()

    def get_serializer_class(self):
        """Use different serializers for create, update, and retrieve."""
        if self.action == 'create':
            return QRCodeCreateSerializer
        elif self.action in ('update', 'partial_update'):
            return QRCodeUpdateSerializer
        return QRCodeSerializer

    def perform_create(self, serializer):
        """Save with the current user."""
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        """Soft delete the QR code instead of hard deleting it."""
        instance = self.get_object()
        instance.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def qrcode_preview(request):
    """Generate a QR code image for preview without saving to the database."""
    serializer = QRCodeCreateSerializer(
        data=request.data,
        context={'request': request},
    )
    serializer.is_valid(raise_exception=True)
    validated_data = dict(serializer.validated_data)

    url = validated_data.pop('url', None)
    data = validated_data.pop('data', None)
    use_url_shortening = validated_data.get('use_url_shortening', False)

    if url:
        content = url
    else:
        content = data

    if not content:
        return Response(
            {'detail': "Either 'url' or 'data' must be provided."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # If using URL shortening, get the short code from the request and create short URL
    if use_url_shortening:
        short_code = request.data.get('short_code')
        if short_code:
            # Create the short URL for preview
            base_url = request.build_absolute_uri('/')
            content = f'{base_url}go/{short_code}/'

    qr_instance = QRCode(
        id=uuid.uuid4(),
        created_by=request.user,
        content=content,
        **{
            key: value
            for key, value in validated_data.items()
            if key
            in {
                'name',
                'qr_type',
                'qr_format',
                'size',
                'error_correction',
                'border',
                'background_color',
                'foreground_color',
                'use_url_shortening',
            }
        },
        image_file='preview.png',
    )

    image_path = QRCodeGenerator.generate_qr_code(qr_instance)
    image_url = QRCodeGenerator.get_file_url(image_path)

    return Response({'image_url': image_url}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def redirect_view(request, short_code):
    """Redirect endpoint for shortened URLs."""
    try:
        qr_code = QRCode.objects.get(short_code=short_code)
    except QRCode.DoesNotExist:
        msg = 'QR Code not found'
        raise Http404(msg)

    # Redirect to the dashboard if QR code is soft-deleted
    if qr_code.deleted_at:
        return redirect('dashboard')

    qr_code.increment_scan_count()

    if qr_code.original_url:
        return redirect(qr_code.original_url)

    return Response(
        {"error": "No redirect URL available for this QR code"},
        status=status.HTTP_400_BAD_REQUEST,
    )
