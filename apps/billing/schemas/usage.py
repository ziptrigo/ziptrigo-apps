from datetime import date

from pydantic import BaseModel, Field


class BillingUsageAddedSchema(BaseModel):
    """Credits that came in during the interval, by kind of transaction."""

    type: str = Field(description='`purchase`, `promo`, `welcome` or `adjustment`.')
    credits: int


class BillingUsageAppSchema(BaseModel):
    """One app's usage over the whole interval."""

    app: str = Field(description='The app label (`file_transfer`, `receipts`, ...), or `other`.')
    name: str
    used: int = Field(description='Net credits used: spends minus refunds.')


class BillingUsagePeriodSchema(BaseModel):
    """One period of the interval (a whole day, week, month, quarter or year, even where the
    interval cuts the first or last one short)."""

    start: date = Field(description='The first day of the period; weeks start on Monday.')
    label: str = Field(description='The period in words, in the public wording (`Nov 2026`).')
    total: int = Field(
        description='Net credits used in the period; negative when refunds exceed spends.'
    )
    apps: dict[str, int] = Field(description='Net credits used per app label.')


class BillingUsageSchema(BaseModel):
    """What each app used from `date_from` to `date_to`."""

    date_from: date
    date_to: date
    group: str = Field(description='`day`, `week`, `month`, `quarter` or `year`.')
    timezone: str = Field(
        description="The account's time zone, which decides where a day starts (UTC when unset)."
    )
    balance: int = Field(description='The current balance, whatever the interval.')
    used: int = Field(description='Net credits used in the interval (spends minus refunds).')
    refunded: int = Field(
        description='Credits refunded in the interval; already subtracted from `used`.'
    )
    added: list[BillingUsageAddedSchema]
    apps: list[BillingUsageAppSchema]
    periods: list[BillingUsagePeriodSchema] = Field(
        description='Every period, oldest first, empty ones included.'
    )
