/* ============================================================
   STAFF DATA — HigherGrade Tutoring
   Shared by about.html and admin.html.
   LocalStorage key 'highergrade_staff_v1' persists admin edits.
   ============================================================ */

const STAFF_CATEGORIES = [
  { id: 'organizers',          label: 'Organizers',          desc: 'The students who conceived, designed, and built this camp from scratch.' },
  { id: 'teaching_staff',      label: 'Teaching Staff',      desc: 'Senior HDSB students who lead the lessons and write the exams.' },
  { id: 'teaching_assistants', label: 'Teaching Assistants', desc: 'TAs who sit alongside students during practice periods and help debug tough problems.' },
  { id: 'general_staff',       label: 'General Staff',       desc: 'Logistics, tech, and the behind-the-scenes crew keeping the camp running.' },
  { id: 'supervisors',         label: 'Supervisors',         desc: 'Faculty advisors providing oversight, safety, and mentorship.' },
  { id: 'partners',            label: 'Partners',            desc: 'Organizations and individuals who made this program possible.' },
];

const DEFAULT_STAFF = [
  {
    id: 'alex-chen', category: 'organizers',
    name: 'Alex Chen', role: 'Lead Organizer',
    image: 'placeholder_team_alex_chen_headshot.png',
    quote: 'I wanted to build the math experience I wish I had in Grade 9.',
    age: '17', school: 'Abbey Park High School — Grade 12',
    gender: 'Male', pronouns: 'he / him',
    interests: 'Number theory, jazz piano, chess, competitive math, Oakville pizza discourse.',
    bio: "Hi! I'm Alex — a Grade 12 HDSB student and the one who kicked this whole thing off.\n\nI fell in love with number theory after stumbling onto a Numberphile video about Fermat primes late one night, and I've been hooked ever since. I've written the CEMC Euclid three years running and reached the CMO qualifier last year.\n\nThe idea for this camp came out of frustration — I was sitting through a slow Grade 9 math class and realized I'd learned more in one afternoon of self-study than the whole semester. I wanted to give the next wave of Grade 9s a preview of what math can actually feel like.",
    transcript: 'Grade 12 HDSB student. 98% MCV4U, 96% MHF4U. Euclid 2026 top 10%. CMO Qualifier 2024.',
  },
  {
    id: 'jordan-park', category: 'organizers',
    name: 'Jordan Park', role: 'Curriculum Design',
    image: 'placeholder_team_jordan_park_headshot.png',
    quote: 'A good proof should click into place like a satisfying puzzle.',
    age: '16', school: 'Abbey Park High School — Grade 11',
    gender: 'Non-binary', pronouns: 'they / them',
    interests: 'Geometry, origami, peer tutoring, documentary films.',
    bio: "I'm Jordan, a Grade 11 HDSB student, and I handle most of the curriculum design and lesson planning.\n\nGeometry is my favourite unit — there's something deeply satisfying about a proof that just clicks into place. I'm the one writing most of the Unit 4 material, plus the 'Putting it all together' day where we combine everything we've learned about shapes and coordinate geometry.\n\nOutside of camp work, I tutor at my school's peer tutoring program and run a small origami club.",
    transcript: 'Grade 11 HDSB student. 95% MCR3U, 97% MPM2D.',
  },
  {
    id: 'maya-patel', category: 'teaching_staff',
    name: 'Maya Patel', role: 'Exam & Competitions Lead',
    image: 'placeholder_team_maya_patel_headshot.png',
    quote: "Math contests aren't about speed — they're about seeing problems differently.",
    age: '17', school: 'Abbey Park High School — Grade 12',
    gender: 'Female', pronouns: 'she / her',
    interests: 'Competitive math, cross-country running, bullet journalling.',
    bio: "I'm Maya, a Grade 12 HDSB student. I've participated in CEMC contests every year since Grade 7 and scored top 25% on the Euclid last year.\n\nAt the camp, I design the weekend unit tests and the two final exams (Applications + Thinking) to closely match what Grade 9 students will actually see in class — plus a few stretch questions to push the strongest of you.\n\nWhen I'm not doing math, I'm probably running — I'm on my school's cross-country team.",
    transcript: 'Grade 12 HDSB student. 97% MHF4U, 95% MDM4U. Euclid 2026 top 25%. CEMC Cayley/Fermat gold pins.',
  },
  {
    id: 'priya-nair', category: 'teaching_staff',
    name: 'Priya Nair', role: 'Data & Linear Relations Lead',
    image: 'placeholder_team_priya_nair_headshot.png',
    quote: "Statistics is everywhere — most people just haven't been given the right lens.",
    age: '17', school: 'Abbey Park High School — Grade 12',
    gender: 'Female', pronouns: 'she / her',
    interests: 'Statistics, competitive Scrabble, Studio Ghibli films.',
    bio: "I'm Priya, a Grade 12 HDSB student, and I lead the Data unit and part of Linear Relations.\n\nI'm particularly passionate about making statistics feel intuitive — it's everywhere in daily life and usually taught in the most abstract way possible. I want students to leave this camp able to look at any graph or dataset and actually understand what it's telling them.\n\nOutside of math, I play competitive Scrabble (yes, that's a thing).",
    transcript: 'Grade 12 HDSB student. 95% MDM4U, 94% MHF4U. 3 years peer tutoring at Abbey Park.',
  },
  {
    id: 'riley-singh', category: 'teaching_assistants',
    name: 'Riley Singh', role: 'Teaching Assistant',
    image: 'placeholder_team_riley_singh_headshot.png',
    quote: "Stuck on a problem? That's where it gets interesting.",
    age: '16', school: 'Abbey Park High School — Grade 11',
    gender: 'Male', pronouns: 'he / him',
    interests: 'Calculators, coding, ultimate frisbee.',
    bio: "Grade 11 HDSB student and camp TA. My job is to sit alongside you during practice periods when you're stuck on a problem. I was a Grade 9 here two years ago — I remember exactly what MTH1W feels like the first time, and I'm here to make that easier for you.",
    transcript: 'Grade 11 HDSB student. 94% MCR3U. MTH1W: 96%. MPM2D: 95%.',
  },
  {
    id: 'sam-rivera', category: 'general_staff',
    name: 'Sam Rivera', role: 'Logistics & Outreach',
    image: 'placeholder_team_sam_rivera_headshot.png',
    quote: "Behind every smooth camp day is a spreadsheet I'll never show you.",
    age: '16', school: 'Abbey Park High School — Grade 11',
    gender: 'Non-binary', pronouns: 'they / them',
    interests: 'Statistics, economics, sports analytics, project management.',
    bio: "I'm Sam — Grade 11 HDSB student and the person keeping this whole operation organized.\n\nSchedules, school partnerships, parent communication, day-of logistics — that's all me. I'll be the voice behind most of the emails your family receives from us.\n\nI'm less of a pure math person and more of a 'math-adjacent' one; I love stats, data viz, and how math shows up in economics and sports.",
    transcript: '',
  },
  {
    id: 'leo-zhang', category: 'general_staff',
    name: 'Leo Zhang', role: 'Tech & Resources',
    image: 'placeholder_team_leo_zhang_headshot.png',
    quote: 'Nothing satisfies me more than a clean solution to a hard problem.',
    age: '16', school: 'Abbey Park High School — Grade 11',
    gender: 'Male', pronouns: 'he / him',
    interests: 'Algorithms, cryptography, Project Euler, restoring old graphing calculators.',
    bio: "I'm Leo — Grade 11 HDSB student in charge of anything that involves a screen. This website, the problem set PDFs, and the online practice portal are all my work.\n\nI love the overlap between math and computer science, especially algorithms, cryptography, and number theory. If you notice a weird typo or broken link, please let me know.",
    transcript: '',
  },
  {
    id: 'ms-thompson', category: 'supervisors',
    name: 'Ms. Thompson', role: 'Faculty Advisor',
    image: 'placeholder_team_ms_thompson_headshot.png',
    quote: "These students built something I couldn't have imagined at their age.",
    age: '42', school: 'Abbey Park High School — Math Department',
    gender: 'Female', pronouns: 'she / her',
    interests: 'Teaching, mentorship, gardening, crime novels.',
    bio: "I've been teaching math at Abbey Park High School for 14 years, most of them with a Grade 9 class on my timetable.\n\nWhen Alex first pitched me this camp in October, I said yes before they'd finished the sentence. My role here is purely supervisory — I provide oversight, safety, and a quiet presence, but everything you see here was built by the students.",
    transcript: 'Ontario Certified Teacher (OCT). B.Sc. Mathematics, University of Toronto. B.Ed., York University. 14 years teaching secondary math.',
  },
  {
    id: 'mr-daniels', category: 'partners',
    name: 'Mr. Daniels', role: 'HDSB Curriculum Liaison',
    image: 'placeholder_team_mr_daniels_headshot.png',
    quote: 'Student-led programs like this are exactly what modern education needs.',
    age: '48', school: 'Halton District School Board',
    gender: 'Male', pronouns: 'he / him',
    interests: 'Curriculum development, community programs, chess.',
    bio: 'Curriculum coordinator at HDSB. I connected this team with board resources, reviewed their MTH1W alignment against the Ontario curriculum expectations, and helped secure space at Abbey Park.',
    transcript: '',
  },
];

const STAFF_STORAGE_KEY = 'highergrade_staff_v1';

function getStaff() {
  try {
    const stored = localStorage.getItem(STAFF_STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) return parsed;
    }
  } catch (e) { /* fall through */ }
  return DEFAULT_STAFF.slice();
}

function saveStaff(arr) {
  localStorage.setItem(STAFF_STORAGE_KEY, JSON.stringify(arr));
}

function resetStaff() {
  localStorage.removeItem(STAFF_STORAGE_KEY);
}
