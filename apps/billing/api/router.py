from ninja import Router

#: One router for the billing API (tag `Credits`); the endpoint modules register on it.
router = Router(tags=['Credits'])
