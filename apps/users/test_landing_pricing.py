from decimal import Decimal

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings
from django.urls import reverse

from .billing_models import BillingConfiguration


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class LandingPricingTests(TestCase):
    def sections(self):
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        return [soup.select_one('#membresias'), soup.select_one('#precios')]

    def test_both_sections_show_active_database_prices_and_updates(self):
        config = BillingConfiguration.objects.create(
            version=1, status='active', currency='EUR',
            plus_monthly=Decimal('8.23'), plus_semiannual=Decimal('45.67'), plus_annual=Decimal('89.12'),
            pro_monthly=Decimal('18.34'), pro_semiannual=Decimal('98.76'), pro_annual=Decimal('175.43'),
        )
        for section in self.sections():
            for plan in ('plus', 'pro'):
                for period in ('monthly', 'semiannual', 'annual'):
                    price = section.select_one(f'[data-plan="{plan}"][data-period="{period}"]')
                    actual = price.get_text(' ', strip=True).replace(',', '.')
                    self.assertIn(str(getattr(config, f'{plan}_{period}')), actual)
                    self.assertIn('EUR', actual)
        BillingConfiguration.objects.filter(pk=config.pk).update(status='retired')
        BillingConfiguration.objects.create(
            version=2, status='active', plus_monthly=Decimal('12.34'),
        )
        BillingConfiguration.objects.create(version=3, status='draft', plus_monthly=Decimal('999.99'))
        for section in self.sections():
            price = section.select_one('[data-plan="plus"][data-period="monthly"]').get_text()
            self.assertIn('12.34', price.replace(',', '.'))
            self.assertNotIn('999', section.get_text())
            self.assertIn('No disponible', section.select_one('[data-plan="pro"]').get_text())

    def test_no_configuration_does_not_invent_paid_prices(self):
        for section in self.sections():
            prices = section.select('[data-plan]')
            self.assertEqual(len(prices), 6)
            self.assertTrue(all('No disponible' in item.get_text() for item in prices))
            self.assertNotIn('6.90', section.get_text())
            self.assertNotIn('16.90', section.get_text())
            self.assertNotIn('72 €', section.get_text())

    def test_zero_is_a_configured_price_not_missing(self):
        BillingConfiguration.objects.create(version=1, status='active', plus_monthly=Decimal('0.00'))
        for section in self.sections():
            price = section.select_one('[data-plan="plus"][data-period="monthly"]').get_text()
            self.assertIn('0.00', price.replace(',', '.'))
            self.assertNotIn('No disponible', price)
