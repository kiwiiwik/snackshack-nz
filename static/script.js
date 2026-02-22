let currentUser = null;
let currentPin = "";
let keypadMode = "";
let pendingUserId = null;
let autoLogoutTimer; 
const AUTO_LOGOUT_DELAY = 10000;

const barcodeInput = document.getElementById('barcodeInput');
const statusDiv = document.getElementById('status');
const pinBtn = document.getElementById('pinBtn');

// AUTO LOGOUT
function resetLogoutTimer() {
    clearTimeout(autoLogoutTimer);
    if (currentUser) autoLogoutTimer = setTimeout(logout, AUTO_LOGOUT_DELAY);
}
document.addEventListener('click', resetLogoutTimer);
document.addEventListener('keypress', resetLogoutTimer);
document.addEventListener('mousemove', resetLogoutTimer);

// PIN LOGIC
function updatePinButton() {
    if (currentUser.has_pin) {
        pinBtn.innerText = "🔓 Remove PIN";
        pinBtn.className = "btn-pin-remove";
    } else {
        pinBtn.innerText = "🔒 Set PIN";
        pinBtn.className = "btn-pin-set";
    }
}
function handlePinBtn() {
    if (currentUser.has_pin) {
        if (confirm("Remove PIN?")) {
            fetch('/remove_pin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: currentUser.user_id})
            }).then(r => r.json()).then(data => {
                if(data.success) { alert("PIN Removed."); currentUser.has_pin = false; updatePinButton(); }
            });
        }
    } else { openKeypad('setpin'); }
}

// UNDO LOGIC
function undoLast() {
    if(!currentUser) return;
    fetch('/undo_last', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({user_id: currentUser.user_id})
    })
    .then(r => r.json())
    .then(data => {
        if(data.status === 'success') {
            updateBalance(data.new_balance);
            statusDiv.innerHTML = "↩ UNDONE: " + data.undo_info.product;
            statusDiv.style.backgroundColor = "#fff3cd";
            const tileStock = document.getElementById('stock-' + data.undo_info.barcode);
            if (tileStock) {
                tileStock.innerText = "(" + data.undo_info.restored_stock + " left)";
                tileStock.className = "stock-tag";
            }
        } else { alert("Undo Failed: " + data.message); }
    });
}

// KEYPAD
function openKeypad(mode, userId=null) {
    keypadMode = mode; pendingUserId = userId; currentPin = "";
    document.getElementById('pinDisplay').innerText = "";
    document.getElementById('keypadTitle').innerText = (mode === 'setpin') ? "Create New PIN" : "Enter PIN";
    document.getElementById('keypadModal').classList.remove('hidden');
}
function closeKeypad() { document.getElementById('keypadModal').classList.add('hidden'); }
function keyPress(key) {
    if (key === 'clear') currentPin = "";
    else if (key === 'enter') handleKeypadSubmit();
    else if (currentPin.length < 8) currentPin += key;
    document.getElementById('pinDisplay').innerText = "*".repeat(currentPin.length);
}
function handleKeypadSubmit() {
    if (keypadMode === 'login') attemptLogin(pendingUserId, currentPin);
    else if (keypadMode === 'admin') {
        if (currentPin === '19571953') {
            closeKeypad();
            document.getElementById('loginSection').classList.add('hidden');
            document.getElementById('stocktakeSection').classList.remove('hidden');
            document.getElementById('stockBarcode').focus();
        } else { alert("Incorrect Admin Code"); currentPin = ""; }
    } else if (keypadMode === 'setpin') setUserPin(currentPin);
}

// LOGIN
function selectUser(userId, userName, hasPin) {
    if (hasPin) openKeypad('login', userId); else attemptLogin(userId, "");
}
function attemptLogin(userId, pin) {
    fetch('/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({user_id: userId, pin: pin})
    }).then(r => r.json()).then(data => {
        if(data.success) {
            closeKeypad(); currentUser = data;
            document.getElementById('loginSection').classList.add('hidden');
            document.getElementById('scanSection').classList.remove('hidden');
            document.getElementById('welcomeMsg').innerText = "Hi " + data.name;
            updateBalance(data.balance); updatePinButton(); barcodeInput.focus(); resetLogoutTimer();
        } else { alert(data.error || "Login Failed"); currentPin = ""; document.getElementById('pinDisplay').innerText = ""; }
    });
}
function setUserPin(newPin) {
    if(newPin.length < 4) { alert("PIN must be 4 digits"); return; }
    fetch('/set_pin', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({user_id: currentUser.user_id, pin: newPin})
    }).then(r => r.json()).then(data => {
        if(data.success) { alert("PIN Set!"); currentUser.has_pin = true; updatePinButton(); closeKeypad(); }
        else { alert("Error: " + data.error); currentPin = ""; }
    });
}
function logout() { window.location.href = "/"; }
function updateBalance(amt) {
    const el = document.getElementById('balanceMsg');
    el.innerText = "$" + amt.toFixed(2);
    el.style.color = amt < 0 ? "#dc3545" : "#28a745";
}

// SCAN
function triggerScan(code) { barcodeInput.value = code; barcodeInput.dispatchEvent(new KeyboardEvent('keypress', { 'key': 'Enter' })); }
barcodeInput.addEventListener('keypress', function (e) {
    if (e.key === 'Enter') {
        const code = barcodeInput.value; barcodeInput.value = ''; statusDiv.innerText = "Processing...";
        fetch('/scan', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({user_id: currentUser.user_id, barcode: code})
        }).then(r => r.json()).then(data => {
            if(data.status === 'success') {
                (function() { const a = new (window.AudioContext || window.webkitAudioContext)(); const o = a.createOscillator(); const g = a.createGain(); o.connect(g); g.connect(a.destination); o.type = 'sine'; o.frequency.setValueAtTime(880, a.currentTime); g.gain.setValueAtTime(0.3, a.currentTime); g.gain.exponentialRampToValueAtTime(0.001, a.currentTime + 0.4); o.start(a.currentTime); o.stop(a.currentTime + 0.4); })();
                statusDiv.innerHTML = "✅ " + data.product + " ($" + data.price + ") <button onclick='undoLast()' class='btn-undo'>↩ Undo</button>";
                statusDiv.style.backgroundColor = "#d4edda"; updateBalance(data.new_balance);
                const tile = document.getElementById('stock-' + code);
                if (tile) { tile.innerText = data.new_stock > 0 ? "(" + data.new_stock + " left)" : "EMPTY"; tile.className = data.new_stock > 0 ? "stock-tag" : "out-stock"; }
            } else if (data.status === 'new_item') { statusDiv.innerText = "🆕 New: " + data.product; statusDiv.style.backgroundColor = "#fff3cd"; }
            else { statusDiv.innerText = "❌ " + data.message; statusDiv.style.backgroundColor = "#f8d7da"; }
        });
    }
});
document.addEventListener('click', function(e) {
    if (!document.getElementById('scanSection').classList.contains('hidden') && e.target.tagName !== 'INPUT' && !document.getElementById('keypadModal').contains(e.target)) barcodeInput.focus();
});

// STOCKTAKE
const stockBarcode = document.getElementById('stockBarcode');
const stockQty = document.getElementById('stockQty');
stockBarcode.addEventListener('keypress', function(e) { if(e.key === 'Enter') stockQty.focus(); });
function submitStock() {
        fetch('/restock', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({barcode: stockBarcode.value, qty: parseInt(stockQty.value)})
    }).then(r => r.json()).then(data => {
        document.getElementById('stockStatus').innerText = "✅ Stock Added!";
        stockBarcode.value = ''; stockQty.value = 1; stockBarcode.focus();
    });
}