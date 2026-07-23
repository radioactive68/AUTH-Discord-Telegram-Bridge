from django import forms
from .models import DTBSettings, ForwardRule, TelegramUser


class DTBSettingsForm(forms.ModelForm):
    class Meta:
        model = DTBSettings
        fields = [
            'telegram_bot_token', 'discord_bot_token', 'discord_guild_id',
            'alliance_id', 'autostart_bot',
        ]
        widgets = {
            'telegram_bot_token': forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
            'discord_bot_token': forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
            'discord_guild_id': forms.TextInput(attrs={'class': 'form-control'}),
            'alliance_id': forms.NumberInput(attrs={'class': 'form-control'}),
        }
        help_texts = {
            'alliance_id': 'Leave empty to disable membership check.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_tokens = {}
        for name in ('telegram_bot_token', 'discord_bot_token'):
            if self.instance and self.instance.pk:
                self._original_tokens[name] = getattr(self.instance, name, '') or ''
            if getattr(self.instance, name, ''):
                self.fields[name].widget.attrs['placeholder'] = (
                    'Already set (hidden for security) - leave blank to keep'
                )
                self.fields[name].help_text = (
                    'Token is saved. Leave blank to keep the current value.'
                )

    def save(self, commit=True):
        instance = super().save(commit=False)
        for name in ('telegram_bot_token', 'discord_bot_token'):
            new_val = (self.cleaned_data.get(name) or '').strip()
            if not new_val and name in self._original_tokens and self._original_tokens[name]:
                setattr(instance, name, self._original_tokens[name])
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ForwardRuleForm(forms.ModelForm):
    class Meta:
        model = ForwardRule
        fields = [
            'name', 'discord_channel_id', 'discord_channel_name',
            'telegram_target', 'telegram_target_type', 'keyword_filter',
            'is_enabled', 'priority',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'discord_channel_id': forms.TextInput(attrs={'class': 'form-control'}),
            'discord_channel_name': forms.TextInput(attrs={'class': 'form-control'}),
            'telegram_target': forms.TextInput(attrs={'class': 'form-control'}),
            'keyword_filter': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. ops,cta,roum (comma-separated)',
            }),
        }


class TelegramUserLinkForm(forms.Form):
    telegram_username = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '@username or username',
        }),
        label='Telegram Username',
    )


class TelegramGroupForm(forms.Form):
    chat_id = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. @channel_name or -100xxxxxxxxxx',
        }),
        label='Telegram Chat ID or @username',
    )
