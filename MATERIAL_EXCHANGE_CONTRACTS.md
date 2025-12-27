# Material Exchange Contract Validation System

## Overview

The Material Exchange contract validation system automates the verification and processing of sell and buy orders using EVE Online's ESI API. It ensures that players create proper in-game contracts matching their orders and notifies users and admins through the Alliance Auth in-app notification system.

## Architecture

### Components

#### 1. **ESIClient Extensions** (`indy_hub/services/esi_client.py`)

Added two new methods to support corporation contract operations:

- `fetch_corporation_contracts(corporation_id, character_id)` - Fetches all corporation contracts
- `fetch_corporation_contract_items(corporation_id, contract_id, character_id)` - Fetches items in a specific contract

**Scope Required**: `esi-contracts.read_corporation_contracts.v1`

#### 2. **Contract Validation Task** (`indy_hub/tasks/material_exchange_contracts.py`)

**Main Functions:**

- **`validate_material_exchange_sell_orders()`** - Runs every 5 minutes
  - Finds all pending sell orders
  - Fetches corp contracts via ESI
  - Matches contracts to orders by:
    - Contract type = `item_exchange`
    - Issuer = seller (character)
    - Acceptor = corporation
    - Location = configured structure
    - Items match exactly (type_id, quantity)
  - Updates status and notifies:
    - User: error if contract not found
    - Admins: success if contract verified

- **`check_completed_material_exchange_contracts()`** - Runs every 10 minutes
  - Monitors approved sell orders
  - Checks if their contracts are completed in ESI
  - Updates order status to `PAID` and notifies user

- **`handle_material_exchange_buy_order_created(order_id)`** - Triggered on order creation
  - Notifies admins immediately
  - Buy orders don't require contract validation (admin-driven)

#### 3. **Buy Order Signal** (`indy_hub/signals.py`)

Registered signal handler on `MaterialExchangeBuyOrder.post_save`:
- Triggers when a new buy order is created
- Queues admin notification via Celery task
- Non-blocking: errors are logged but don't break the save operation

#### 4. **Celery Beat Schedule** (`indy_hub/schedules.py`)

Two new periodic tasks:

| Task | Schedule | Priority | Purpose |
|------|----------|----------|---------|
| `indy-hub-validate-sell-orders` | Every 5 min | 4 | Check ESI contracts for pending sell orders |
| `indy-hub-check-completed-contracts` | Every 10 min | 4 | Monitor contract completion status |

## Workflow Diagrams

### Sell Order Flow

```
User submits Sell Order
  ↓
Status = PENDING
  ↓
[Every 5 min] validate_material_exchange_sell_orders()
  ↓
  ├─ Contract found + Items match → Status = APPROVED
  │    ├─ Notify Admins: "Order approved, ready for payment"
  │    └─ [Every 10 min] check_completed_material_exchange_contracts()
  │         ├─ Contract status = "completed" → Status = PAID
  │         └─ Notify User: "Payment verified"
  │
  └─ Contract NOT found → Status = REJECTED
       └─ Notify User: Error message with instructions
```

### Buy Order Flow

```
User submits Buy Order
  ↓
Status = PENDING
  ↓
Signal triggers immediately
  ↓
[Async] handle_material_exchange_buy_order_created()
  ↓
Notify Admins: "New buy order, approve to proceed"
  ↓
Admin approves → Status = APPROVED
  ↓
Admin delivers via contract/trade → Status = DELIVERED
  ↓
User confirms receipt → Status = COMPLETED
```

## Notification System

Uses `indy_hub.notifications.notify_user()` and `notify_multi()`:

- **Messages**: Sent via Alliance Auth in-app notification system
- **Fallback**: Discord DM (if `INDY_HUB_DISCORD_DM_ENABLED=True`)
- **Include**: Link to the material exchange module and order details

### Example Notifications

**Sell Order - Contract Found:**
```
To: Admins
Subject: Sell Order Approved
Body: User [seller] wants to sell 1000x Evaporite Deposits for 50,000,000 ISK.
Contract verified via ESI. Ready for payment processing.
```

**Sell Order - Contract Not Found:**
```
To: Seller
Subject: Sell Order Contract Mismatch
Body: We could not verify your sell order for 1000x Evaporite Deposits.
Please create an item exchange contract with:
- Recipient: [Corp Name]
- Items: Evaporite Deposits
- Quantity: 1000
- Location: [Structure Name]
```

**Buy Order - Created:**
```
To: Admins
Subject: New Buy Order
Body: [Buyer] wants to buy 500x Vanadium for 25,000,000 ISK.
Stock available at creation: 500x
Review and approve to proceed with delivery.
```

## Model Changes

The following models already support this workflow:

- **`MaterialExchangeSellOrder`**
  - `status`: pending → approved → paid → completed
  - `notes`: Stores contract ID and validation messages
  - `approved_at`: Timestamp when approved

- **`MaterialExchangeBuyOrder`**
  - `status`: pending → approved → delivered → completed
  - `approved_by`: Admin who approved
  - `delivery_method`: contract, trade, or hangar access

- **`MaterialExchangeConfig`**
  - `corporation_id`: Which corp owns the hub
  - `structure_id`: Where hub is located
  - `structure_name`: Display name (cached)

## ESI Integration Requirements

For contract validation to work, at least one character in the corporation must have:
- Scope: `esi-contracts.read_corporation_contracts.v1`
- Role: Typically "Director" or equivalent in EVE

The system automatically finds an available character with this scope.

## Error Handling

All tasks are designed to be robust:

1. **Missing Token**: Logs error, doesn't crash, will retry next cycle
2. **API Rate Limit**: Handled by ESIClient retry logic with backoff
3. **Contract Mismatch**: Rejects order, notifies user with instructions
4. **Database Errors**: Logged, individual orders skipped, task continues
5. **Forbidden 403**: ESITokenError, token deleted, task stops (must fix permissions)

## Future Enhancements

Possible improvements for later:

- [ ] In-game mail notifications (requires ESI mail writing scope)
- [ ] Automatic payment via wallet API (requires write scopes)
- [ ] Order expiration (auto-reject if not verified within X days)
- [ ] Bulk order support (multiple items in one order)
- [ ] Price adjustments based on market data
- [ ] Admin dashboard with pending orders

## Testing the System

### Manual Testing

1. **Create a Sell Order:**
   ```python
   from indy_hub.models import MaterialExchangeSellOrder, MaterialExchangeConfig
   from django.contrib.auth.models import User
   
   config = MaterialExchangeConfig.objects.first()
   user = User.objects.first()
   
   order = MaterialExchangeSellOrder.objects.create(
       config=config,
       seller=user,
       type_id=1234567,
       type_name="Test Material",
       quantity=1000,
       unit_price=50000,
       total_price=50000000,
   )
   print(f"Created order {order.id} - Status: {order.status}")
   ```

2. **In EVE, create matching contract:**
   - Type: Item Exchange
   - To: Your corporation
   - Items: The test material (exact quantity)
   - Location: Configured structure

3. **Run validation manually:**
   ```bash
   python manage.py celery call indy_hub.tasks.material_exchange_contracts.validate_material_exchange_sell_orders
   ```

4. **Check results:**
   ```python
   order.refresh_from_db()
   print(f"Status: {order.status}")
   print(f"Notes: {order.notes}")
   ```

### Unit Test Example

```python
from django.test import TestCase
from unittest.mock import patch
from indy_hub.tasks.material_exchange_contracts import validate_material_exchange_sell_orders
from indy_hub.models import MaterialExchangeSellOrder, MaterialExchangeConfig

class ContractValidationTest(TestCase):
    def setUp(self):
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456,
            structure_id=789012,
            structure_name="Test Structure",
        )
        self.user = User.objects.create_user(username="testuser")
        self.order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.user,
            type_id=1234567,
            type_name="Test Material",
            quantity=1000,
            unit_price=50000,
            total_price=50000000,
        )

    @patch('indy_hub.tasks.material_exchange_contracts.shared_client')
    def test_contract_validation_success(self, mock_client):
        # Mock ESI responses
        mock_client.fetch_corporation_contracts.return_value = [{
            'contract_id': 1,
            'type': 'item_exchange',
            'issuer_id': self.user.id,
            'acceptor_id': self.config.corporation_id,
            'start_location_id': self.config.structure_id,
            'status': 'active',
        }]
        
        mock_client.fetch_corporation_contract_items.return_value = [{
            'type_id': 1234567,
            'quantity': 1000,
            'is_included': True,
        }]
        
        # Run validation
        validate_material_exchange_sell_orders()
        
        # Check results
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'approved')
        self.assertIn('Contract validated', self.order.notes)
```

## Configuration

No new settings required, but ensure existing config is set:

```python
# testauth/settings/base.py
INDY_HUB_DISCORD_DM_ENABLED = True  # Send Discord DMs for notifications
CELERY_ALWAYS_EAGER = False  # Use async tasks (not for testing)
```

## Performance Considerations

- **Database**: Uses `.filter()` and `.first()` for efficiency
- **ESI Calls**: Batched per task run (not per order)
- **Caching**: Contract fetch happens once per task execution
- **Backoff**: ESIClient handles rate limits automatically

## Security Notes

- **Token Scopes**: Only uses corporation contracts scope (read-only)
- **User Validation**: Checks seller's character IDs match contract issuer
- **Admin Checks**: Uses Django permissions (`can_manage_material_exchange`)
- **Error Messages**: Never expose token details or internal IDs
