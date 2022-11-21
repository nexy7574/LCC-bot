import timetable from '../../utils/timetable.json';
import { useSession, signIn } from "next-auth/react"
import style from '../styles/Home.module.css';
import {useState} from "react";


const days = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];


function currentTimeTable(_day_diff = null) {
    let now = new Date();
    if(_day_diff) {
        now = new Date(now.valueOf() + (86400 * _day_diff))
    }
    let day = days[now.getDay()];
    const today_timetable = timetable[day];
    return today_timetable || []
}

function is_old(lesson, now) {
    let lesson_start = new Date();
    lesson_start.setHours(lesson.start[0], lesson.start[1], 0)
    let lesson_end = new Date();
    lesson_end.setHours(lesson.end[0], lesson.end[1], 0);
    if(lesson_end > now) {
        return true;
    }
}


function currentLesson() {
    const now = new Date();
    const date = now.toLocaleDateString("en-GB");
    const time = now.toLocaleTimeString("en-GB");
    const hour = parseInt(time.split(":")[0]);
    const minute = parseInt(time.split(":")[1]);
    const day = days[now.getDay()];
    const today_timetable = currentTimeTable();
    console.debug(
        now,
        date,
        time,
        hour,
        minute,
        day,
        today_timetable
    )

    for(let lesson of today_timetable) {
        let lesson_start = new Date();
        lesson_start.setHours(lesson.start[0], lesson.start[1], 0)
        let lesson_end = new Date();
        lesson_end.setHours(lesson.end[0], lesson.end[1], 0);
        if(lesson_end > now) {
            if(lesson_start <= now) {
                console.debug("Returning", lesson)
                lesson.start_timestamp = lesson_start.valueOf()
                lesson.end_timestamp = lesson_end.valueOf()
                return lesson
            }
        }
    }
    return {
        "name": "No Current Lesson",
        "start": [0, 0],
        "end": [0, 0],
        "tutor": "",
        "room": "",
        "start_timestamp": (new Date()).setHours(0, 0, 0),
        "end_timestamp": (new Date()).setHours(23, 59, 59)
    }
}


function normaliseTime(t) {
    return `${t[0].toString().padStart(2, "0")}:${t[1].toString().padStart(2, "0")}`
}


function TimeTableBlock(props) {
    const lesson = props.lesson;
    let [open, setOpen] = useState(false);
    const _onClick = () => {setOpen(!open)};
    return (
        <div className={style.timetableListContainer} onClick={_onClick}>
            <h3>{lesson.name || 'error'}</h3>
            <span hidden={open}><i>Click/tap to expand</i></span>
            <ul className={style.timetableList} hidden={!open}>
                <li>Start: {normaliseTime(lesson.start || [0, 0])}</li>
                <li>End: {normaliseTime(lesson.end || [23, 59])}</li>
                <li>Room: {lesson.room}</li>
                <li>Tutor: {lesson.tutor}</li>
            </ul>
        </div>
    )
}


export default function Home() {
    let [dayOfTheWeek, setDay] = useState((new Date()).getDay());
    const incrementDay = (by) => {
        let new_DOTW = dayOfTheWeek + by;
        if(new_DOTW>6) {
            new_DOTW = 0
        }
        else if (new_DOTW < 0) {
            new_DOTW = 6
        }
        setDay(new_DOTW);
    }
    const { data: session } = useSession();
    if(!session) {
        return (
            <div style={{textAlign: "center"}}>
                <button onClick={() => signIn()}>Sign In (with Discord)</button>
            </div>
        )
    }
    let current_lesson = currentLesson();
    // let current_timetable = currentTimeTable(dayOfTheWeek);
    return (
        <div>
            <h1 style={{textAlign: "center", marginBottom: "4rem"}}>Hello, {session.user.name}.</h1>
            <div style={{textAlign: "center"}}>
                <h1>Current lesson:</h1>
                <p>{current_lesson.name}</p>
                <progress value={current_lesson.start_timestamp} max={current_lesson.end_timestamp}></progress>
            </div>
            <br/>
            <hr style={{width: "75%"}}/>
            <br/>
            <h2 style={{textAlign: "center"}}>Full timetable:</h2>
            <div className={style.todaysTimeTable}>
                <div>
                    <h3 style={{textAlign: "center"}}>{days[dayOfTheWeek]}</h3>
                </div>
                <div style={{display: "flex", justifyContent: "space-between"}}>
                    <button onClick={() => {incrementDay(-1)}}>Previous day</button>
                    <button onClick={() => {incrementDay(1)}}>Next day</button>
                </div>
                {
                    currentTimeTable(dayOfTheWeek).map(
                        (lesson) => {
                            return <TimeTableBlock key={lesson.name} lesson={lesson}/>
                        }
                    )
                }
            </div>
        </div>
    )
}
