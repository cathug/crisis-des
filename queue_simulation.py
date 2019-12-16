'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and helpseeker arrivals.  

    Helpseekers will renege when they loose patience waiting in the queue

    For more details about the Simpy syntax, please visit
    https://simpy.readthedocs.io/en/latest/contents.html

    Primer in Discrete Event Simulation:
    https://www.academia.edu/35846791/Discrete_Event_Simulation._It_s_Easy_with_SimPy_
'''

import simpy, random
# from scipy.stats import poisson




# Globals
NUM_HELPSEEKERS_IN_SYSTEM = 0
NUM_HELPSEEKERS_IN_QUEUE = 0
NUM_HELPSEEKERS_IN_PRIORITY_QUEUE = [0, 0, 0]
SIMULATION_DURATION = 1440
NUM_COUNSELLING_PROCESS = 16
SEED = 728




################################################################################
# Classes
################################################################################

class ServiceOperation:
    '''
        Service Operation Class to emulate counsellor availability
    '''

    lunch_break = 60 # 60 minute lunch break
    num_counsellors = 5


    def __init__(self, 
                 env,
                 counsellor_id,
                 service_duration):
        self.env = env
        self.process = env.process(self.handle_helpseeker() )
        self.counsellor = f'Counsellor {counsellor_id}'

        self.service_duration = service_duration

    #---------------------------------------------------------------------------
    
    def handle_helpseeker(self):
        '''
            helpseeker handler
        '''

        while True:
            # start counselling for the first 6 hours
            print(f'{self.counsellor} starts shift at {self.env.now}')
            yield self.env.timeout(self.service_duration)

            # give lunch break
            # no interrupts thrown at first half of lunch break
            print(f'{self.counsellor} Getting a lunch break at {self.env.now}')
            yield self.env.process(self.half_lunch_break() )

            # in the second half, interrupt when the queue grows too long
            try: 
                yield self.env.process(self.half_lunch_break() )
            except simpy.Interrupt:
                print(f"Counsellor {self.counsellor}'s lunch break "
                    "has been cut short.")

            yield self.env.timeout(self.service_duration)
            print(f'{self.counsellor} shift ends at {self.env.now}')

    
    #---------------------------------------------------------------------------

    def half_lunch_break(self):
        '''
            Give counsellors half a lunch break
            Call this twice to give a full lunch break
        '''
        yield self.env.timeout(self.lunch_break // 2)

#-------------------------------------------------------end of Counsellors class

class Helpseeker:
    '''
        Helpseeker Class to create a helpseeker
    '''

    mean_chat_duration = 60 # average chat no longer than 60 minutes
    mean_interarrival_time = 7.0 # mean time between helpseekers arriving
    risklevel_weights = {
        'CRISIS':   .05,
        'HIGH':     .015,
        'MEDIUM':   .16,
        'LOW':      .82,
    } # Distribution of LOW/MEDIUM/HIGH/CRISIS - 82%/16%/1.5%/0.5%

    usertype_weights = {
        'REPEATED_USER': .05, 
        'REGULAR_USER': .95, 
    } # Distribution of Repeated Users - 95%/5%

    mean_renege_time = 7.0  # mean patience before reneging 
                            # specify this as a float

    def __init__(self,
                 env,
                 helpseeker_id,
                 counselling_process):

        '''
            param:
                env - simpy environment instance
                helpseeker_id - an assigned helpseeker id
                counselling_process - Openup counselling process resource
        '''

        self.env = env
        self.arrival_time = None
        self.counselling_process = counselling_process
        self.user = f'Helpseeker {helpseeker_id}'
        self.risklevel = self.assign_risklevel()
        self.user_status = self.assign_user_status()

        # start creating a chatroom session
        self.process = env.process(self.session() )

    #---------------------------------------------------------------------------

    def session(self):
        '''
            Function to create a chatroom session
        '''

        # simulate the interarrivals between users
        interarrival_time = self.assign_interarrival_time()
        yield self.env.timeout(interarrival_time)

        self.arrival_time = self.env.now
        print(f'{self.user} has accepted TOS.  '
            f'Chat session created at t = {self.env.now}.')


        with self.counselling_process.request() as request:
            # wait for counsellor or renege
            patience = self.assign_renege_time()
            results = yield request | self.env.timeout(patience)
            
            if request in results:
                # counsellor now serving helpseeker
                time_now = self.env.now
                queue_time = time_now - self.arrival_time
                print(f'{self.user} now being served at {time_now}.  '
                    f'User spent {queue_time} in the queue.')

                chat_duration = self.assign_chat_duration()
                yield self.env.timeout(chat_duration)
                print(f'{self.user} chat session terminated successfully at t ='
                    f' {self.env.now}. Chat lasted {chat_duration}')

            else:
                # helpseeker reneged
                print(f'{self.user} reneged after '
                    f'spending t = {patience} in the queue.')

    #---------------------------------------------------------------------------

    def assign_interarrival_time(self):
        '''
            Getter to assign interarrival time
            interarrival time follows an exponential distribution
        '''
        lambda_interarrival = 1.0/self.mean_interarrival_time
        return random.expovariate(lambda_interarrival)

    #---------------------------------------------------------------------------

    def assign_chat_duration(self):
        '''
            Getter to assign chat duration
            chat duration follows an exponential distribution
        '''
        lambda_chat_duration = 1.0 / self.mean_chat_duration
        return random.expovariate(lambda_chat_duration)

    #---------------------------------------------------------------------------

    def assign_renege_time(self):
        '''
            Getter to assign patience to helpseeker
            helpseeker patience follows an exponential distribution

        '''
        lambda_renege = 1.0/self.mean_renege_time
        return random.expovariate(lambda_renege)

    #---------------------------------------------------------------------------

    def assign_risklevel(self):
        '''
            Getter to assign helpseeker risklevel
        '''

        return random.choices(list(self.risklevel_weights.keys() ), 
            list(self.risklevel_weights.values() ) )[0]

    #---------------------------------------------------------------------------

    def assign_user_status(self):
        '''
            Getter to assign repeated user status
        '''
        return random.choices(list(self.usertype_weights.keys() ), 
            list(self.usertype_weights.values() ) )[0]

################################################################################
# Main Function
################################################################################

def main():
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print('Initializing OpenUp Queue Simulation')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n')
    random.seed(SEED) # comment out line if not reproducing results

    # create environment
    env = simpy.Environment()
    # counsellor = Counsellor(env, 1, 8, 1)
    
    counselling_process = simpy.Resource(env, capacity=NUM_COUNSELLING_PROCESS)

    helpseekers = []
    start_time = env.now
    duration = 0


    # create helpseekers
    for i in range(1, 120):
        helpseekers.append(Helpseeker(env, i, counselling_process) )

    print(f'Total number of helpseekers created: {len(helpseekers)}\n\n')

    env.run(until=SIMULATION_DURATION) # daily simulation at the queue


if __name__ == '__main__':
    main()