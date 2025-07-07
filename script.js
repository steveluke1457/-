/* ========= CONFIG ========= */
const REPO      = 'YOUR_GH_USERNAME/YOUR_REPO';
const BRANCH    = 'main';
const FILE_PATH = 'content.json';

/* hashed admin password (SHA‑256 of: mySuperPassword)  */
const ADMIN_HASH = '825668d50b08bbc359bb8c4213d40a5c3718a42571648f9cce77e7abe592fea2';

/* ========= RUNTIME ========= */
const editBtn   = document.getElementById('editBtn');
let editMode    = false;
let json        = {};

/* 1. Load content.json and inject */
fetch(FILE_PATH + '?cache=' + Date.now())
  .then(r => r.json())
  .then(data => {
    json = data;
    document.querySelectorAll('[data-key]').forEach(el=>{
        const k = el.dataset.key;
        if(json[k]) el.innerText = json[k];
    });
});

editBtn.onclick = async () => {
  if(!editMode){
    const pwd = prompt('Enter admin password:');
    const hash = CryptoJS.SHA256(pwd).toString();
    if(hash !== ADMIN_HASH){
      alert('Wrong password');
      return;
    }
    enableEdit();
  }else{
    await saveEdits();
    disableEdit();
  }
};

function enableEdit(){
  editMode = true;
  editBtn.innerText = '💾 Save';
  document.querySelectorAll('[data-key]').forEach(el=>{
    el.contentEditable = true;
    el.style.outline = '2px dashed var(--neon)';
  });
}

function disableEdit(){
  editMode = false;
  editBtn.innerText = '✏️ Edit';
  document.querySelectorAll('[data-key]').forEach(el=>{
    el.contentEditable = false;
    el.style.outline = 'none';
  });
}

async function saveEdits(){
  document.querySelectorAll('[data-key]').forEach(el=>{
    json[el.dataset.key] = el.innerText.trim();
  });
  const token = prompt('Paste a GitHub **PAT** with repo scope.\nIt is only stored in your browser.');
  if(!token) return alert('Save aborted.');

  const getRes = await fetch(`https://api.github.com/repos/${REPO}/contents/${FILE_PATH}?ref=${BRANCH}`,{
    headers:{Authorization:`token ${token}`}
  });
  const {sha} = await getRes.json();

  const body = {
    message: `content update ${new Date().toISOString()}`,
    branch: BRANCH,
    sha,
    content: btoa(unescape(encodeURIComponent(JSON.stringify(json, null, 2))))
  };

  const res = await fetch(`https://api.github.com/repos/${REPO}/contents/${FILE_PATH}`,{
    method:'PUT',
    headers:{
      'Content-Type':'application/json',
      Authorization:`token ${token}`
    },
    body:JSON.stringify(body)
  });

  if(res.ok){ alert('Saved to GitHub!'); }
  else{ alert('GitHub save failed'); }
}
