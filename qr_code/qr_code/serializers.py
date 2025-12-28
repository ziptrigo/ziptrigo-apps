from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from .models import QRCode
from .services import QRCodeGenerator


class QRCodeCreateSerializer(serializers.ModelSerializer[QRCode]):
    """Serializer for creating QR codes."""

    url = serializers.CharField(
        write_only=True,
        required=False,
        help_text='Original URL for QR code (or any text)',
    )  # type: ignore[assignment]
    data = serializers.CharField(
        write_only=True,
        required=False,
        help_text='Alternative to url - any data to encode',
    )  # type: ignore[assignment]

    class Meta:
        model = QRCode
        fields = [
            'id',
            'name',
            'qr_type',
            'qr_format',
            'size',
            'error_correction',
            'border',
            'background_color',
            'foreground_color',
            'use_url_shortening',
            'url',
            'data',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, data):
        """Ensure either url or data is provided and validate URL format when type is URL."""
        if not data.get('url') and not data.get('data'):
            raise serializers.ValidationError("Either 'url' or 'data' must be provided.")
        if data.get('url') and data.get('data'):
            raise serializers.ValidationError("Provide either 'url' or 'data', not both.")

        # Validate URL format when qr_type is 'url'
        qr_type = data.get('qr_type')
        content = data.get('url') or data.get('data')

        if qr_type == 'url' and content:
            url_validator = URLValidator()
            try:
                url_validator(content)
            except DjangoValidationError:
                raise serializers.ValidationError(
                    {'url': 'Please provide a valid URL when type is URL.'}
                )

        return data

    def create(self, validated_data):
        """Create QR code and generate image."""
        # Extract write-only fields
        url = validated_data.pop('url', None)
        data = validated_data.pop('data', None)

        # Determine content
        if url:
            validated_data['original_url'] = url
            if validated_data.get('use_url_shortening'):
                # Content will be the shortened URL, set after save
                validated_data['content'] = url  # Temporary
            else:
                validated_data['content'] = url
        else:
            validated_data['content'] = data

        # Set the user
        validated_data['created_by'] = self.context['request'].user

        # Create instance (will generate short_code if needed)
        instance = QRCode.objects.create(**validated_data)

        # If using URL shortening, update content to shortened URL
        if instance.use_url_shortening and instance.short_code:
            redirect_url = instance.get_redirect_url()
            if redirect_url:
                instance.content = redirect_url
                instance.save(update_fields=['content'])

        # Generate QR code image
        image_path = QRCodeGenerator.generate_qr_code(instance)
        instance.image_file = image_path
        instance.save(update_fields=['image_file'])

        return instance


class QRCodeUpdateSerializer(serializers.ModelSerializer[QRCode]):
    """Serializer for updating QR codes (name only)."""

    class Meta:
        model = QRCode
        fields = [
            'id',
            'name',
            'qr_type',
            'content',
            'original_url',
            'qr_format',
            'use_url_shortening',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'qr_type',
            'content',
            'original_url',
            'qr_format',
            'use_url_shortening',
            'created_at',
            'updated_at',
        ]

    def update(self, instance, validated_data):
        """Update only the name field."""
        instance.name = validated_data.get('name', instance.name)
        instance.save(update_fields=['name'])
        return instance


class QRCodeSerializer(serializers.ModelSerializer):
    """Serializer for retrieving QR code details."""

    image_url = serializers.SerializerMethodField()
    redirect_url = serializers.SerializerMethodField()

    class Meta:
        model = QRCode
        fields = [
            'id',
            'qr_type',
            'content',
            'original_url',
            'use_url_shortening',
            'short_code',
            'qr_format',
            'size',
            'error_correction',
            'border',
            'background_color',
            'foreground_color',
            'image_file',
            'image_url',
            'redirect_url',
            'scan_count',
            'last_scanned_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_image_url(self, obj):
        """Get full URL to the QR code image."""
        request = self.context.get('request')
        if request and obj.image_file:
            return request.build_absolute_uri(f'{settings.MEDIA_URL}{obj.image_file}')
        return None

    def get_redirect_url(self, obj):
        """Get the redirect URL if applicable."""
        return obj.get_redirect_url()
